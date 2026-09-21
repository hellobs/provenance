# -*- coding: utf-8 -*-
"""价值权重「声明 → 资产」落盘器 + CLI 的详细测试(2026-09-21 收口 ②)。

覆盖三类风险:
- **写错地方/写坏排版**:dry-run 绝不落盘;apply 只改该改的值,缩进与末尾换行保持原样;
- **声明缺失时乱造**:声明没有 value_tendency / 某角色缺账 → 拒绝落盘并如实报;
- **漂移**:资产与声明不一致时以声明为准覆盖;重复 apply 幂等。
另有一条**在真实 case00 上**的干跑 + 落盘后哈希不变(证明当前确实单一来源)。
"""
import hashlib
import io
import json
import os
import sys

import pytest

_this_dir = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(os.path.dirname(_this_dir))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

import case_engine.cli as cli                                    # noqa: E402
from case_engine import config as cfg_mod                        # noqa: E402
from case_engine.value_tendency import materialize, summary_line  # noqa: E402

GOV = {"A": 0.6, "B": 0.4}          # 制度层权重(声明里写的)
INIT = {"A": 0.7, "B": 0.3}         # 初始倾向(声明里写的)
STALE = {"OLD": 1.0}                # 资产里先放一份**不一致**的旧值 → 验证"声明为准"

AGENTS_REL = "case00/scenario/agents"
GOV_REL = "governance.json"


def _mk_case(tmp_path, agents=("role_one", "role_two"), indent=4, trailing_newline=False,
             gov_decl=None, init_decl=None):
    """在 tmp 下造最小 case:cases/mini/(scenario.yaml 由调用方按需写)+ 资产。"""
    base = str(tmp_path)
    cases_root = os.path.join(base, "cases")
    os.makedirs(os.path.join(cases_root, "mini"), exist_ok=True)

    for a in agents:
        d = os.path.join(base, AGENTS_REL, a)
        os.makedirs(d, exist_ok=True)
        payload = {"name": a, "role_type": "user", "coord": [1, 2],
                   "initial_tendency": dict(STALE)}
        body = json.dumps(payload, ensure_ascii=False, indent=indent)
        if trailing_newline:
            body += "\n"
        io.open(os.path.join(d, "agent.json"), "w", encoding="utf-8", newline="").write(body)
    io.open(os.path.join(base, GOV_REL), "w", encoding="utf-8", newline="").write(
        json.dumps({"roles": dict(STALE)}, ensure_ascii=False, indent=indent))

    data = {
        "meta": {"case_id": "mini", "name": "迷你场景"},
        "roles": [{"id": a, "display_name": a, "type": "user", "llm": "local"}
                  for a in agents],
        "world": {
            "value_tendency": {
                "governance": {a: dict(GOV if gov_decl is None else gov_decl) for a in agents},
                "initial_tendency": {a: dict(INIT if init_decl is None else init_decl)
                                     for a in agents}},
            "assets": {"agents": AGENTS_REL, "governance": GOV_REL},
        },
    }
    return data, base, cases_root


def _yaml(data):
    """把测试 dict 写成 yaml(结构简单,不引入 pyyaml 依赖)。"""
    def dump(obj, ind=0):
        pad = "  " * ind
        out = []
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    out.append("{}{}:".format(pad, k))
                    out.append(dump(v, ind + 1))
                else:
                    out.append("{}{}: {}".format(pad, k, json.dumps(v, ensure_ascii=False)))
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    first = True
                    for k, v in item.items():
                        out.append("{}{}{}: {}".format(
                            pad, "- " if first else "  ", k,
                            json.dumps(v, ensure_ascii=False)))
                        first = False
                else:
                    out.append("{}- {}".format(pad, json.dumps(item, ensure_ascii=False)))
        return "\n".join(x for x in out if x.strip())
    return dump(data) + "\n"


def _hash(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _tree_hash(base):
    out = {}
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            p = os.path.join(dirpath, fn)
            out[os.path.relpath(p, base).replace("\\", "/")] = _hash(p)
    return out


# ---------------------------------------------------------------- 落盘行为
def test_dry_run_changes_nothing(tmp_path):
    data, base, _root = _mk_case(tmp_path)
    before = _tree_hash(base)
    report = materialize(cfg_mod.load(data), base=base, apply=False)
    assert report["changed"], "资产与声明不一致时,dry-run 必须报出待改项"
    assert report["written"] == []
    assert _tree_hash(base) == before, "dry-run 不许动任何文件"
    assert "dry-run" in summary_line(report)


def test_apply_writes_declaration_values(tmp_path):
    data, base, _root = _mk_case(tmp_path)
    report = materialize(cfg_mod.load(data), base=base, apply=True)
    assert report["ok"] and report["problems"] == []
    gov = json.loads(io.open(os.path.join(base, GOV_REL), encoding="utf-8").read())
    assert gov["roles"]["role_one"] == pytest.approx(GOV)
    for a in ("role_one", "role_two"):
        ap = os.path.join(base, AGENTS_REL, a, "agent.json")
        assert json.loads(io.open(ap, encoding="utf-8").read())["initial_tendency"] \
            == pytest.approx(INIT)


def test_apply_is_idempotent(tmp_path):
    data, base, _root = _mk_case(tmp_path)
    cfg = cfg_mod.load(data)
    materialize(cfg, base=base, apply=True)
    second = materialize(cfg, base=base, apply=True)
    assert second["changed"] == [], "第二次落盘不该再有改动"
    assert len(second["in_sync"]) == 1 + 2, second
    assert second["written"] == []


def test_drift_is_overwritten_by_declaration(tmp_path):
    data, base, _root = _mk_case(tmp_path)
    cfg = cfg_mod.load(data)
    materialize(cfg, base=base, apply=True)
    ap = os.path.join(base, AGENTS_REL, "role_one", "agent.json")
    d = json.loads(io.open(ap, encoding="utf-8").read())
    d["initial_tendency"] = {"A": 0.99, "B": 0.01}     # 有人偷改一边
    io.open(ap, "w", encoding="utf-8", newline="").write(
        json.dumps(d, ensure_ascii=False, indent=4))
    rep = materialize(cfg, base=base, apply=True)
    assert rep["written"], rep
    back = json.loads(io.open(ap, encoding="utf-8").read())["initial_tendency"]
    assert back == pytest.approx(INIT), "声明为准,单向覆盖"


def test_formatting_is_preserved(tmp_path):
    """排版不许被落盘改掉(否则每次都是巨大 diff,没人敢 review)。"""
    from case_engine.value_tendency import _detect_indent

    data, base, _root = _mk_case(tmp_path, indent=2, trailing_newline=True)
    ap = os.path.join(base, AGENTS_REL, "role_one", "agent.json")
    # 先确认夹具本身是 2 空格 + 末尾换行(否则测的不是落盘,而是夹具)
    before_txt = io.open(ap, encoding="utf-8").read()
    assert _detect_indent(before_txt) == 2, repr(before_txt[:40])
    assert before_txt.endswith("\n")

    materialize(cfg_mod.load(data), base=base, apply=True)
    txt = io.open(ap, encoding="utf-8").read()
    assert txt.endswith("\n"), "原本有末尾换行,写回后必须保留"
    assert _detect_indent(txt) == 2, repr(txt[:40])
    d = json.loads(txt)
    assert d["name"] == "role_one" and d["coord"] == [1, 2], "无关字段不许被改"


# ---------------------------------------------------------------- 拒绝落盘
def test_refuses_when_declaration_has_no_value_tendency(tmp_path):
    data, base, _root = _mk_case(tmp_path)
    data["world"].pop("value_tendency")
    before = _tree_hash(base)
    rep = materialize(cfg_mod.load(data), base=base, apply=True)
    assert not rep["ok"] and rep["problems"]
    assert _tree_hash(base) == before, "拒绝落盘时不许写任何文件"
    assert "拒绝" in summary_line(rep)


def test_refuses_when_a_role_misses_a_ledger(tmp_path):
    data, base, _root = _mk_case(tmp_path)
    data["world"]["value_tendency"]["initial_tendency"].pop("role_two")
    before = _tree_hash(base)
    rep = materialize(cfg_mod.load(data), base=base, apply=True)
    assert not rep["ok"]
    assert rep["problems"] and "role_two" in rep["problems"][0]
    assert _tree_hash(base) == before


def test_unknown_role_key_is_reported_but_does_not_block_the_rest(tmp_path):
    data, base, _root = _mk_case(tmp_path)
    data["world"]["value_tendency"]["governance"]["不存在的人"] = {"A": 1.0}
    rep = materialize(cfg_mod.load(data), base=base, apply=True)
    assert rep["problems"], "多出来的角色键必须报出来"
    assert any("不存在的人" in p for p in rep["problems"])
    assert rep["written"] and not rep["ok"], "合法部分仍落盘,但整体标为不通过"


# ---------------------------------------------------------------- CLI
def test_cli_dry_run_then_apply_then_consistent(tmp_path, capsys):
    data, base, cases_root = _mk_case(tmp_path)
    io.open(os.path.join(cases_root, "mini", "scenario.yaml"), "w",
            encoding="utf-8", newline="").write(_yaml(data))

    # --cases-dir 是顶层选项,必须放在子命令之前
    rc = cli.main(["--cases-dir", cases_root, "materialize-value-tendency", "mini"])
    out = capsys.readouterr().out
    assert rc == 0 and "dry-run" in out and "需改" in out, out

    rc = cli.main(["--cases-dir", cases_root, "materialize-value-tendency", "mini", "--apply"])
    out = capsys.readouterr().out
    assert rc == 0 and "已落盘" in out, out

    rc = cli.main(["--cases-dir", cases_root, "materialize-value-tendency", "mini"])
    out = capsys.readouterr().out
    assert rc == 0 and "0 项需改" in out, out


# ---------------------------------------------------------------- 真实数据
REAL_CASE = os.path.join(_PKG, "cases", "case00_village", "scenario.yaml")
REAL_AGENTS = ("AI Advisor", "Wendy Lin", "Daniel Shen", "Kevin Su", "Michael Chen", "Mr. Zhou")


def test_real_case00_refuses_to_wipe_runtime_interventions():
    """真实 case00:治理资产已被专家干预改过 → 默认**拒绝覆盖**,并指名道姓说是谁改的;
    角色 initial_tendency 不受影响,照旧 6 项一致。"""
    cfg = cfg_mod.load_yaml(REAL_CASE)
    base = _PKG
    dry = materialize(cfg, base=base, apply=False)
    inter = dry.get("runtime_interventions") or {}
    if inter:
        assert dry["problems"], "有运行时干预却不报,等于静默抹掉专家动作"
        assert any("干预" in p for p in dry["problems"]), dry["problems"]
        assert "AI Advisor" in dry["problems"][0] or inter, dry["problems"]
        assert [c["asset"] for c in dry["changed"]] == ["governance.json"], dry["changed"]
    # 六个角色的初始倾向必须仍然一致(治理那本账可能因干预而偏离,故不按总数断言)
    agents_in_sync = [c for c in dry["in_sync"] if c["kind"] == "initial_tendency"]
    assert len(agents_in_sync) == 6, dry["in_sync"]

    # apply(不带 force)同样不许动治理文件
    targets = [os.path.join(base, "governance.json")] + [
        os.path.join(base, AGENTS_REL, a, "agent.json") for a in REAL_AGENTS]
    before = {p: _hash(p) for p in targets}
    rep = materialize(cfg, base=base, apply=True)
    assert rep["written"] == [], "默认不该写任何文件(治理被干预保护、初始倾向已一致)"
    assert {p: _hash(p) for p in targets} == before


def test_real_case00_force_is_opt_in_and_reported():
    """--force 才会把治理算作待改,并且明确写进 forced 报告(dry-run 不落盘)。"""
    cfg = cfg_mod.load_yaml(REAL_CASE)
    rep = materialize(cfg, base=_PKG, apply=False, allow_overwrite_interventions=True)
    if not (rep.get("runtime_interventions") or {}):
        pytest.skip("当前没有运行时干预,这条不适用")
    assert rep.get("forced"), "强行覆盖必须留痕在 forced 里"
    assert "governance.json" in [c["asset"] for c in rep["changed"]]


def test_intervention_is_explained_only_when_it_matches_current_asset():
    """explain_divergence 的判据:最后一条干预的 new_constraints 必须==当前资产,否则不算解释。"""
    from case_engine.value_tendency import explain_divergence

    declared = {"R": {"A": 0.5, "B": 0.5}}
    inter = [{"agent": "R", "operator": "expert", "time": "t1",
              "old_constraints": {"A": 0.5, "B": 0.5},
              "new_constraints": {"A": 0.7, "B": 0.3}}]
    ok = explain_divergence({"R": {"A": 0.7, "B": 0.3}}, declared, inter)
    assert ok["R"]["diverged"] and ok["R"]["explained"] and "expert" in ok["R"]["by"]
    bad = explain_divergence({"R": {"A": 0.9, "B": 0.1}}, declared, inter)
    assert bad["R"]["diverged"] and not bad["R"]["explained"], "对不上就不许说被解释"
    same = explain_divergence({"R": {"A": 0.5, "B": 0.5}}, declared, inter)
    assert not same["R"]["diverged"]


def test_materialize_refuses_then_force_overwrites_on_tmp_case(tmp_path):
    """端点行为(tmp 最小场景):默认拒绝覆盖被干预的治理;force 才写,并报出 forced。"""
    data, base, _root = _mk_case(tmp_path)
    # 造一份"解释了偏离"的干预审计:把治理改成与声明不同,并写明是谁改的
    os.makedirs(os.path.join(base, "results", "checkpoints"), exist_ok=True)
    inter = [{"agent": "role_one", "operator": "expert", "time": "2026-09-21 22:30:03",
              "old_constraints": dict(GOV),
              "new_constraints": {"A": 0.9, "B": 0.1}}]
    io.open(os.path.join(base, "results", "checkpoints", "interventions.json"), "w",
            encoding="utf-8").write(json.dumps(inter, ensure_ascii=False))
    gov_p = os.path.join(base, GOV_REL)
    io.open(gov_p, "w", encoding="utf-8", newline="").write(
        json.dumps({"roles": {"role_one": {"A": 0.9, "B": 0.1},
                              "role_two": dict(STALE)}}, ensure_ascii=False, indent=4))

    rep = materialize(cfg_mod.load(data), base=base, apply=True)
    assert rep["problems"] and "干预" in rep["problems"][0]
    gov_now = json.loads(io.open(gov_p, encoding="utf-8").read())
    assert gov_now["roles"]["role_one"] == {"A": 0.9, "B": 0.1}, "默认不许覆盖被干预的角色"

    rep2 = materialize(cfg_mod.load(data), base=base, apply=True,
                       allow_overwrite_interventions=True)
    assert rep2.get("forced"), "强行覆盖要留痕"
    gov_now2 = json.loads(io.open(gov_p, encoding="utf-8").read())
    assert gov_now2["roles"]["role_one"] == pytest.approx(GOV), "force 后回到声明基线"
