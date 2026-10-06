# -*- coding: utf-8 -*-
"""种子链:一个 master 摊到两条流 + 记环境身份,且不破坏默认(非确定)路径。

钉住的判据(对应设计稿 §五 的验收,这里不烧 GPU,先把逻辑钉住):
  A. **不传 master 什么都不做** —— 这是敢进主干的唯一理由;既有语料的语义不受影响。
     连环境探针都不许发(不新增网络调用/子进程)。
  B. 派生**与 PYTHONHASHSEED 无关** —— 用内建 hash() 的话跨进程会变,
     "种子链"就退化成"每个进程重新随机"。
  C. `world` 流必须带 run_id 盐 —— 否则同一批多条记录共用同一条世界轨迹,
     n 条样本等于同一条样本跑 n 遍。
  D. 启用时**环境身份要落进旁证**,探针失败只留原因不抛(第二十轮体检的缺口)。
  E. 缺 `--run-id` 时先定名再派生(D-2)。
"""
import json
import os
import random
import subprocess
import sys

from case01 import rngchain

HERE = os.path.dirname(os.path.abspath(__file__))          # .../provenance/case01/tests
# 包根要往上**两级**——只数一次 dirname 会拿到 case01,子进程里就成了
# "No module named 'case01'(这条是我第一版真踩过的坑,留在这里当注释)。
PKG = os.path.abspath(os.path.join(HERE, "..", ".."))      # .../provenance


def _world_stream(master, run_id):
    """在解释器里真跑一遍:播种→抽 3 个数,返回这 3 个数的指纹。"""
    random.seed(rngchain.derive_world_seed(master, run_id))
    return [round(random.random(), 12), random.choice(list("abcdefg")),
            round(random.uniform(0, 1), 12)]


def test_没有_master_时链子完全不介入(monkeypatch, tmp_path):
    monkeypatch.delenv(rngchain.ENV_MASTER, raising=False)
    monkeypatch.delenv(rngchain.ENV_LLM_SEED, raising=False)
    assert rngchain.resolve_master(None) is None
    assert rngchain.apply_chain(None) is None          # 不动 RNG、不写 env
    assert rngchain.derive_world_seed(None) is None
    assert rngchain.resolve_master("") in (None, "")   # 空串不是种子


def test_同master同run_id_抽出的世界流一致而不同run_id不同():
    a = _world_stream(202610061, "run-x")
    b = _world_stream(202610061, "run-x")
    c = _world_stream(202610061, "run-y")
    d = _world_stream(202610062, "run-x")
    assert a == b, "同一 (master, run_id) 必须复现同一条世界流: {}".format((a, b))
    assert a != c, "不同 run_id 共用一条世界流 = 把 n 条样本做成 1 条"
    assert a != d, "不同 master 必须抽出不同流(否则种子根本没生效)"


def test_派生不随_pythonhashseed_改变():
    """子进程换 PYTHONHASHSEED 重算:内建 hash() 会在这里红。"""
    code = (
        "import sys;sys.path.insert(0,{!r});from case01 import rngchain;"
        "print(rngchain.derive_world_seed(123456,'r1'))".format(PKG)
    )
    outs = []
    for hs in ("0", "1", "random"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        # 必须显式带 -u:子进程 stdout 走块缓冲时,不 flush 的父侧读法会拿到空串
        # (本仓已踩过的 Windows 面),rc/err 一起断言,免得空输出被当成"相等"放过。
        r = subprocess.run([sys.executable, "-X", "utf8", "-u", "-c", code],
                           cwd=PKG, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, "子进程失败 rc={} err={}".format(r.returncode, r.stderr[:400])
        outs.append(r.stdout.strip())
    assert outs[0] == outs[1] == outs[2], "派生值随 PYTHONHASHSEED 漂移: {}".format(outs)
    assert outs[0].isdigit(), "派生值应是整数,实得:{!r}".format(outs[0])


def test_非法种子立刻抛不静默(monkeypatch):
    monkeypatch.setenv(rngchain.ENV_MASTER, "abc")
    try:
        rngchain.resolve_master(None)
    except ValueError as exc:
        assert "整数" in str(exc), str(exc)
    else:
        raise AssertionError("非整数种子必须抛,不能当成'没设'")


def test_apply_chain_设两条流并返回可留痕的诊断(monkeypatch, tmp_path):
    monkeypatch.delenv(rngchain.ENV_MASTER, raising=False)
    monkeypatch.setattr(rngchain, "ENV_LLM_SEED", "CASE01_LLM_SEED_TEST")
    info = rngchain.apply_chain(999, run_id="r9", probe=False)
    assert info["model_seed"] == 999, "模型侧必须沿用 master(manifest.seed 记的是它)"
    assert info["world_seed"] == rngchain.derive_world_seed(999, "r9")
    assert os.environ["CASE01_LLM_SEED_TEST"] == "999"
    path = rngchain.write_side_record(str(tmp_path), info)
    assert os.path.basename(path) == "rng.json"
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["rng_scheme"] == rngchain.RNG_SCHEME
    # 旁证文件不带 run.json 的键集语义:它只描述"这条记录是怎么被固定的"
    assert set(on_disk) <= {"rng_scheme", "master", "run_id", "model_seed",
                            "world_seed", "engine_rng", "environment",
                            "engine_think_workers"}


def test_启用种子链时环境身份进旁证且探针不抛(monkeypatch, tmp_path):
    """体检抓到"解释器/服务端模型/哪棵树在产物里全查不到",这条把它记下来。

    用 `127.0.0.1:1`(必然连不上)跑,**同时**验了两件事:探针失败不抛、
    失败原因被如实写进记录 —— 记不上是可惜,静默当"没有"是事故。
    """
    monkeypatch.setenv(rngchain.ENV_LLM_SEED, "")
    info = rngchain.apply_chain(4242, run_id="r-env", base_url="http://127.0.0.1:1")
    env = info["environment"]
    assert env["python_executable"], "解释器路径必须记下来(两批用不同 venv 的那个坑)"
    assert "git_head" in env["git"], env["git"]
    assert env["ollama_version"] == "" and env["ollama_resident"] == [], \
        "端口 1 上不会有 Ollama,拿到值说明探针没走 base_url: {}".format(env)
    assert env.get("ollama_probe_reason"), \
        "连不上要留原因,不能空着让人以为'当时没有驻留模型': {}".format(env)
    path = rngchain.write_side_record(str(tmp_path), info)
    with open(path, encoding="utf-8") as f:
        assert "environment" in json.load(f)


def test_不启用种子链时一个探针都不发(monkeypatch, tmp_path):
    """默认路径的红线:不新增网络调用、不新增子进程、不动 RNG。"""
    calls = []
    monkeypatch.setattr(rngchain, "probe_environment",
                        lambda **kw: calls.append(kw) or {})
    monkeypatch.delenv(rngchain.ENV_MASTER, raising=False)
    monkeypatch.delenv(rngchain.ENV_LLM_SEED, raising=False)
    assert rngchain.apply_chain(None, run_id="r-x") is None
    assert calls == [], "master 为空时不许探针: {}".format(calls)
    assert rngchain.write_side_record(str(tmp_path), None) == ""
    assert os.listdir(str(tmp_path)) == []


def test_run_id_缺名时先定名再派生(monkeypatch):
    """D-2 的回归位:名字既进派生盐也进旁证目录,两者必须是同一个值。"""
    assert rngchain.ensure_run_id("given-name") == "given-name"
    auto = rngchain.ensure_run_id("")
    assert auto.startswith("run-") and len(auto) > 8, auto
    assert rngchain.ensure_run_id("   ").startswith("run-"), "纯空白等于没给名字"
    # 定名之后再派生,必须与"就用这个名字跑一遍"一致(否则旁证与记录不同名)
    assert (rngchain.derive_world_seed(7, auto)
            == rngchain.derive_world_seed(7, rngchain.ensure_run_id(auto)))


def test_留痕失败时不抛只警告(tmp_path, capsys):
    """种子链不能拖垮一局推演:写不进去就明确警告 + 返回空串。"""
    info = {"rng_scheme": rngchain.RNG_SCHEME, "master": 1}
    blocker = tmp_path / "占用这个名字的是文件不是目录"
    blocker.write_text("x", encoding="utf-8")
    bad = os.path.join(str(blocker), "sub")     # 祖先存在但是文件 → makedirs 必失败
    assert rngchain.write_side_record(bad, info) == ""
    out = capsys.readouterr().out
    assert "警告" in out, out
