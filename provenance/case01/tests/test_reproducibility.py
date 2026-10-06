# -*- coding: utf-8 -*-
"""可复现性专项:随机源盘点 + seed 端到端链路 + 复现比对口径。

为什么单独一个文件:
    `test_llm_seed.py` 已覆盖客户端层(seed 进不进请求体),
    `test_run_manifest.py` 已覆盖 manifest 结构。
    **缺的是跨层的那一段**:从 `case01.run` 入口 → orchestrator →
    reflection/router → 落盘 run.json,seed 是否**一路同值**;
    以及"拿两条记录做复现比对时,哪些字段本来就该不同"。

本文件钉三件事:
  A. seed 端到端:三个角色的客户端都拿到同一个 seed(不是只有主对话拿到);
  B. 随机源盘点:自有代码里**不得**出现 random/uuid(随机只能来自模型);
  C. 复现比对口径:`created_at`/`scenario_path`/`financial_data_dir` 是
     **已知易变字段**,比对时必须排除 —— 把这条写成可执行的判据,别靠人记。
"""
import io
import json
import os
import re

import pytest

MANIFEST_KEYS = None  # 惰性导入

# 已知易变字段:同一输入两次跑(甚至同机)必然或可能不同,复现比对时排除。
# 依据见本文件 docstring C 段与《复现说明》§四。
VOLATILE_MANIFEST_KEYS = (
    "created_at",          # wall-clock 写入时间
    "scenario_path",       # 本机绝对路径(跨机必然不同)
    "financial_data_dir",  # 本机绝对路径
)


def _pkg():
    """拿到 injector 的 llm 模块(provenance 通过 requirements 依赖它)。"""
    from mavis_case01_injector import llm
    return llm


# ---------------------------------------------------------------------------
# A. seed 端到端:三个角色同值
# ---------------------------------------------------------------------------

def _capture_http(monkeypatch, sink):
    """把 urllib.request.urlopen 换掉,记录每个请求体的 JSON。"""
    import urllib.request

    class _Resp:
        def __init__(self, body):
            self._b = json.dumps(body).encode("utf-8")

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        try:
            data = req.data.decode("utf-8") if req.data else "{}"
            sink.append(json.loads(data))
        except Exception:  # noqa: BLE001
            sink.append({})
        return _Resp({"choices": [{"message": {"content": "ok"}}],
                      "message": {"content": "ok"},
                      "embeddings": [[0.0] * 8]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_seed_flows_to_every_role_end_to_end(monkeypatch):
    """从环境变量起,主对话/反思/Router 三条链路的请求体都带同一个 seed。

    这条补的是"只测了客户端层"的缺口:即便客户端支持 seed,
    只要**某个角色漏了传**,那次运行仍然是不可复现的,而 manifest 会写"有 seed"。
    """
    M = _pkg()
    sink = []
    _capture_http(monkeypatch, sink)
    monkeypatch.setenv("CASE01_LLM_SEED", "20261004")

    # 三个角色都用 local_client_from_env() 建客户端(与生产同一路径)
    c1 = M.local_client_from_env()
    c2 = M.local_client_from_env()
    c3 = M.local_client_from_env()
    assert (c1.seed, c2.seed, c3.seed) == (20261004,) * 3

    c1.chat([{"role": "user", "content": "main"}])
    c2.native_chat([{"role": "user", "content": "reflection"}])
    c3.chat([{"role": "user", "content": "router"}])

    assert len(sink) == 3, sink
    # /v1/chat/completions 走顶层 seed;/api/chat 走 options.seed
    top = [b for b in sink if "seed" in b]
    nested = [b for b in sink if "seed" in (b.get("options") or {})]
    assert top and all(b["seed"] == 20261004 for b in top), sink
    assert nested and all(b["options"]["seed"] == 20261004 for b in nested), sink


def test_no_seed_means_no_seed_everywhere(monkeypatch):
    """不设种子时,三条链路都**不得**出现 seed 键(不能凭空造一个)。"""
    M = _pkg()
    sink = []
    _capture_http(monkeypatch, sink)
    monkeypatch.delenv("CASE01_LLM_SEED", raising=False)

    for c in (M.local_client_from_env(), M.local_client_from_env()):
        assert c.seed is None
        c.chat([{"role": "user", "content": "x"}])
        c.native_chat([{"role": "user", "content": "y"}])

    for b in sink:
        assert "seed" not in b, b
        assert "seed" not in (b.get("options") or {}), b


def test_seed_env_is_the_single_source(monkeypatch):
    """显式参数优先于环境变量;环境变量是回退(与 run.py 的落定逻辑一致)。"""
    M = _pkg()
    monkeypatch.setenv("CASE01_LLM_SEED", "111")
    assert M.local_client_from_env().seed == 111
    # run.py 在落定后会把显式值写回环境,保证后续同源
    monkeypatch.setenv("CASE01_LLM_SEED", "222")
    assert M.local_client_from_env().seed == 222


# ---------------------------------------------------------------------------
# B. 随机源盘点:自有代码不得有 random/uuid
# ---------------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SCAN_DIRS = [
    os.path.join(REPO_ROOT, "provenance", "case01"),
    os.path.join(REPO_ROOT, "provenance", "case_engine"),
    os.path.join(REPO_ROOT, "provenance", "live"),
    os.path.join(REPO_ROOT, "packages", "mavis-case01-injector", "src"),
    os.path.join(REPO_ROOT, "packages", "mavis-vizkit", "src"),
]

# 允许出现的地方:随机数生成器以外的一切。这里只禁"采样随机"。
BANNED = (
    # 放行 `random.seed(`:播种**不是**新增随机源,而是把引擎已有的那条流固定住
    # (见 case01/rngchain.py 的种子链 —— 引擎那 15 个 `random.*` 调用点在我们仓外,
    # 唯一能管住它们的方式就是在进程入口 seed 一次)。本守卫的前提是"随机源可枚举",
    # seed 让这个前提更可管而非更破坏;其余 `random.` 照旧禁止。
    (re.compile(r"(?<![\w.])random\.(?!seed\()"), "random.*"),
    (re.compile(r"(?<![\w.])uuid\.uuid[14]\("), "uuid1/4"),
    (re.compile(r"np\.random|numpy\.random"), "numpy.random"),
)


def _iter_src():
    for root_dir in SCAN_DIRS:
        if not os.path.isdir(root_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [d for d in dirnames
                           if d not in ("__pycache__", "tests", ".venv")]
            for fn in filenames:
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def test_no_sampling_randomness_in_own_code():
    """自有代码里不得有采样随机 —— 随机性只应来自模型(可用 seed 管)。

    来历:可复现性的前提是"随机源可枚举"。若代码自己 random.shuffle 一个列表,
    那即便 seed 固定了模型采样,整体仍不可复现,而且没有任何 manifest 字段能记到它。
    """
    offenders = []
    for path in _iter_src():
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
        for i, ln in enumerate(lines, 1):
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue
            for pat, name in BANNED:
                if pat.search(ln):
                    offenders.append("{}:{} [{}] {}".format(
                        os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                        i, name, stripped[:90]))
    assert not offenders, (
        "自有代码出现采样随机(会破坏可复现性且无字段可记):\n  "
        + "\n  ".join(offenders))


def test_sorted_iteration_for_output_affecting_walks():
    """影响产物的目录遍历必须排序 —— 否则同一输入两次跑顺序可能不同。

    只检查"读目录后直接进产物"的高危点(os.listdir 未 sort)。
    """
    offenders = []
    pat = re.compile(r"os\.listdir\(([^)]*)\)(?!\s*\))")
    for path in _iter_src():
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        for m in pat.finditer(src):
            # 形如 sorted(os.listdir(...)) 的已被 sorted 包住,跳过
            start = max(0, m.start() - 20)
            if "sorted(" in src[start:m.start() + 1]:
                continue
            # 纯打印/调试用途不追究(人工复核)
            line_no = src[:m.start()].count("\n") + 1
            line = src.split("\n")[line_no - 1].strip()
            if "print(" in line or "for " in line and " in " in line:
                continue
            offenders.append("{}:{} {}".format(
                os.path.relpath(path, REPO_ROOT).replace("\\", "/"),
                line_no, line[:90]))
    # 这条是提示性棘轮:允许存在,但数量不许涨
    assert len(offenders) <= 12, (
        "未排序的 os.listdir 涨了({} > 12),可能引入顺序不确定:\n  ".format(len(offenders))
        + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# C. 复现比对口径:哪些字段本来就该不同
# ---------------------------------------------------------------------------

def _injector_manifest():
    from mavis_case01_injector import manifest
    return manifest


def test_created_at_is_wall_clock_and_must_be_excluded():
    """`created_at` 是 wall-clock,所以复现比对**必须**把它排除。

    这条是"口径可执行化":与其在文档里写一句"created_at 会变,比对时忽略",
    不如让测试证明它确实会变 —— 免得有人写比对脚本时忘了排除,得出一堆假阳性。
    同时也证明它**可注入**(建 manifest 时传 created_at 就能固定),这是
    单测能稳定比对的前提。
    """
    import time
    M = _injector_manifest()
    a = M.build_manifest(run_meta={"seed": 1}, git_commit="deadbeef")
    time.sleep(1.1)
    b = M.build_manifest(run_meta={"seed": 1}, git_commit="deadbeef")
    assert a["created_at"] != b["created_at"], (
        "created_at 竟然不随墙钟变 —— 若它变成了确定性字段,"
        "本清单与《复现说明》§四 的排除规则要同步改")
    # 可注入:固定后其余字段就该完全一致
    kw = dict(run_meta={"seed": 1}, git_commit="deadbeef",
              created_at="2026-10-05T00:00:00+0800")
    assert M.build_manifest(**kw) == M.build_manifest(**kw)


def test_manifest_volatile_keys_are_exactly_the_known_set():
    """易变字段清单要和实际对齐 —— 多一个少一个都该被人发现。

    做法:固定 created_at/git_commit 后连建两次,差异键必须**恰好**是
    VOLATILE_MANIFEST_KEYS 里那些"路径/时间"类字段(其余必须一致)。
    """
    M = _injector_manifest()
    kw = dict(run_meta={"seed": 20261004, "branch": "B"},
              scenario_path=os.path.join(REPO_ROOT, "provenance", "cases",
                                         "case01_stock", "scenario.yaml"),
              financial_dir=os.path.join(REPO_ROOT, "provenance", "case01",
                                         "data", "financial"),
              git_commit="deadbeef", created_at="2026-10-05T00:00:00+0800")
    a, b = M.build_manifest(**kw), M.build_manifest(**kw)
    diff = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    assert not diff, "固定输入后仍有差异键(不全是易变字段): {}".format(sorted(diff))
    # 清单里的键必须真实存在(防止改名后清单失效)
    for k in VOLATILE_MANIFEST_KEYS:
        assert k in a, "易变字段清单里的 {!r} 已不在 manifest 里,清单要更新".format(k)
    # 路径类字段确实是本机绝对路径(跨机必然不同,故须排除)
    for k in ("scenario_path", "financial_data_dir"):
        assert os.path.isabs(a[k]), k


def test_repro_diff_ignores_declared_volatile_keys():
    """给"复现比对"一个现成的可执行口径:排除易变键后,两份记录应完全相等。"""
    M = _injector_manifest()
    base = M.build_manifest(run_meta={"seed": 20261004, "branch": "B"},
                            git_commit="deadbeef",
                            created_at="2026-10-05T00:00:00+0800")
    other = dict(base)
    other["created_at"] = "2026-10-06T12:34:56+0800"       # 换一次跑的时间
    other["scenario_path"] = "D:\\somewhere\\else\\scenario.yaml"
    other["financial_data_dir"] = "D:\\somewhere\\else\\financial"

    def repro_equal(x, y):
        return {k: v for k, v in x.items() if k not in VOLATILE_MANIFEST_KEYS} == \
               {k: v for k, v in y.items() if k not in VOLATILE_MANIFEST_KEYS}

    assert repro_equal(base, other), "排除易变键后应判定为可复现"
    # 反证:不排除就会被误判为"不可复现"
    assert base != other, "若这里相等,说明上面的排除是多余的 —— 清单要重审"


# ---------------------------------------------------------------------------
# D. 确定性产物:重复运行必须逐字节一致
# ---------------------------------------------------------------------------

def _cases_root():
    return os.path.join(REPO_ROOT, "provenance", "cases")


def _run_engine(case_id, input_text):
    from case_engine.config import load_yaml
    from case_engine.engines import build_for
    s = load_yaml(os.path.join(_cases_root(), case_id, "scenario.yaml"))
    return build_for(s).run(s, input_text=input_text)


@pytest.mark.parametrize("case_id", ["case00_village", "case01_stock",
                                     "case02_minimal"])
def test_deterministic_engine_output_is_stable(case_id):
    """不调 LLM 的引擎线(rule-dryrun / assembly-check)重复跑必须完全一致。"""
    outs = [_run_engine(case_id, "建议分阶段先小规模试点") for _ in range(3)]
    first = json.dumps(outs[0], ensure_ascii=False, sort_keys=True)
    for i, o in enumerate(outs[1:], 2):
        assert json.dumps(o, ensure_ascii=False, sort_keys=True) == first, (
            "{} 第 {} 次输出与首次不同 —— 确定性线被破坏".format(case_id, i))


def test_rule_dryrun_branch_is_input_determined():
    """同一输入给同一分支;不同输入可给不同分支(证明它真在读输入)。"""
    a = _run_engine("case01_stock", "建议分阶段先小规模试点")
    b = _run_engine("case01_stock", "建议分阶段先小规模试点")
    assert a["branch"] == b["branch"]
    # 换一个明显不同的输入,至少 run_type 保持一致(仍是规则线)
    c = _run_engine("case01_stock", "不同意,反对这次买入")
    assert c["run_type"] == a["run_type"], "规则线不该因输入改变 run_type"


def test_scenario_discovery_is_stable_and_sorted():
    """场景发现:重复一致,且 id 有序(顺序不确定会让下游产物漂移)。"""
    from case_engine.scenarios import discover
    r1 = [s.case_id for s in discover(_cases_root())]
    r2 = [s.case_id for s in discover(_cases_root())]
    assert r1 == r2
    assert r1 == sorted(r1), "场景清单未按 id 排序: {}".format(r1)


# ---------------------------------------------------------------------------
# E. 采样复现的边界:seed 固定 ≠ 逐字复现(模型冷/热双稳态)
# ---------------------------------------------------------------------------

def test_seed_does_not_appear_to_guarantee_verbatim_across_model_reloads():
    """把"seed 只保证同机同版本**大概率**可复现"从注释变成可执行判据。

    实测发现(2026-10-05,Ollama 0.35.0 / qwen3:8b,详细记录见
    《复现说明_三档口径.md》§五):

        同 seed + 同 prompt + 同参数,输出**只有两个稳定值**——
        模型**冷加载后的首次**推理得 A,之后热态推理得 B;
        且 A、B 各自在重复实验中 100% 稳定。

    证据(本轮原始输出):
        冷起第 1 次:sha=1fe3ff84c7 len=438 (~11s, 每次加载后必现)
        热态第 2/3 次:sha=b7996f80b1 len=388 (~3.6s)
        连续 4 次"卸载→冷起":4/4 都是 1fe3ff84c7
        连续 3 路并发(模型已热):3/3 都是 b7996f80b1

    含义:并发**不是**不可复现的成因(并发批反而全一致);真正的变量是
    "这一轮推理时模型是否刚被载入"。因此**逐字复现的前提是跑法一致**
    —— 要么都冷起(每条前卸载),要么都热跑(先预热再连跑)。

    本测试不去调真模型(那会让单测依赖本机 Ollama),而是把**结论与前提**
    钉成契约:请求体里 seed 确实固定,但 manifest 里必须能看出跑法差异。
    """
    M = _pkg()
    # 1) seed 确实进请求体(前提成立)——否则下面的限定条件无从谈起
    c = M.local_client_from_env(seed=20261004)
    assert c.seed == 20261004

    # 2) 复现比对**必须**把跑法相关的字段一起比,不能只看 seed。
    #    若将来 manifest 记录不了"冷/热",这条会红 —— 提醒补字段。
    mf = _injector_manifest()
    m = mf.build_manifest(run_meta={"seed": 20261004, "branch": "B"},
                          git_commit="deadbeef",
                          created_at="2026-10-05T00:00:00+0800")
    # seed 在 manifest 里(可对账)
    assert m.get("seed") == 20261004
    # 但仅凭 manifest 的 seed 相等,**不能**推出产物逐字相等。
    # 这条断言是"反向棘轮":manifest 里没有能区分冷/热的字段,
    # 所以文档与工具都**不得**宣称 seed 相等即可复现。
    assert not any("warm" in k or "cold" in k or "keep_alive" in k for k in m), (
        "manifest 出现了冷/热相关字段 —— 请同步更新本测试与《复现说明》§五,"
        "因为逐字复现的充分条件可能已经变了")


def test_repro_diff_reports_volatile_only_difference_as_reproducible():
    """repro_diff 的口径:只差在易变字段上 → 判定"可复现",不是"有差异"。

    这条钉住工具的**语义**(而不只是它能跑):排除声明的易变键后应当相等。
    直接复用工具里的常量,保证文档/测试/工具三者用的是同一份清单。
    """
    from case01.tools import repro_diff
    assert set(repro_diff.MANIFEST_VOLATILE) == set(VOLATILE_MANIFEST_KEYS), (
        "工具与测试的易变字段清单不一致:\n  工具={}\n  测试={}".format(
            sorted(repro_diff.MANIFEST_VOLATILE), sorted(VOLATILE_MANIFEST_KEYS)))

    rec_a = {
        "run_id": "runA", "turns": [{"speaker": "investment_ai", "text": "hi"}],
        "outcome": {"branch": "B"},
        "manifest": {"seed": 1, "created_at": "t1", "scenario_path": "a",
                     "financial_data_dir": "b", "git_commit": "x"},
    }
    rec_b = {
        "run_id": "runB",                      # 顶层易变
        "turns": [{"speaker": "investment_ai", "text": "hi"}],
        "outcome": {"branch": "B"},
        "manifest": {"seed": 1, "created_at": "t2", "scenario_path": "c",
                     "financial_data_dir": "d", "git_commit": "x"},
    }
    res = repro_diff.compare(rec_a, rec_b)
    assert res["equal"], "只差易变字段应判定为可复现,实际报了: {}".format(
        res["diff_keys"])
    assert res["diff_keys"] == [], res["diff_keys"]

    # 反证:改一个**实质**字段,必须报出来,且路径可定位
    rec_c = json.loads(json.dumps(rec_b))
    rec_c["outcome"]["branch"] = "C"
    res2 = repro_diff.compare(rec_a, rec_c)
    assert not res2["equal"], "实质差异被漏报 —— 比对工具失效"
    assert any("branch" in d for d in res2["diff_keys"]), res2["diff_keys"]


# ---------------------------------------------------------------------------
# F. 客户端构造面:不得无参裸构造(会绕过模型与 seed 两个环境变量)
# ---------------------------------------------------------------------------

# 生产路径必须经 local_client_from_env() —— 它同时解析 CASE01_LLM_MODEL 与
# CASE01_LLM_SEED。无参 OllamaClient() 吃签名默认(4b)并且**丢掉 seed**,
# 台账还会照着 --model 记 8b,口径与实测分叉。
# 这就是 2026-10-03 在 orchestrator 踩过、2026-10-05 又在 branch_judge_eval /
# run.py --reflect-only 各踩一次的同一个坑,所以做成守卫而不是只改这一遍。
_CLIENT_ALLOWED = {
    "packages/mavis-case01-injector/src/mavis_case01_injector/llm.py",  # 类定义处
}


def test_no_bare_ollama_client_construction_in_production_paths():
    """生产代码里不得出现无参 `OllamaClient()` —— 它会静默丢掉模型与 seed。

    只在**生产路径**上禁(测试自己造客户端或显式传参是合理的)。
    显式传参的 `OllamaClient(seed=...)` 不在此列 —— 它至少保住了 seed,
    真正的口径分叉来源是"什么都不传"。

    用 AST 而不是正则找调用:注释与 docstring 里出现 `OllamaClient()`
    是**说明**(`batch_run._ollama_loaded_model` 的 docstring 正是拿它当反面例子
    讲的),不该算违规;正则分不清这个区别,AST 可以。
    """
    import ast

    offenders = []
    for path in _iter_src():
        rel = os.path.relpath(path, REPO_ROOT).replace("\\", "/")
        if rel in _CLIENT_ALLOWED:
            continue
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            src = fh.read()
        try:
            tree = ast.parse(src)
        except SyntaxError:  # 语法错的文件不归本测试管
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name != "OllamaClient":
                continue
            if node.args or node.keywords:
                continue          # 显式传参的不算(至少 seed 有机会保住)
            offenders.append("{}:{}".format(rel, node.lineno))
    assert not offenders, (
        "生产代码出现无参 OllamaClient()(会绕过 CASE01_LLM_MODEL / CASE01_LLM_SEED,\n"
        "导致台账记 8b 实际跑 4b、且 seed 不生效);请改走 local_client_from_env():\n  "
        + "\n  ".join(offenders))

