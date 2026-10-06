# -*- coding: utf-8 -*-
"""一条命令入口(`serve_all.py`)上的种子透传(2026-10-06 新增)。

起因:README 推的入口是 `serve.cmd` / `python tools/serve_all.py`,而它把 5010 的启动参数
只拼到 `--hold` 为止 —— `--seed`/`--run-id`/`--nodes` 传不进去,而且 `faces()` 里根本没有
这三个参数,所以 `serve_all.py --full --seed X` 会**照样起面、种子却没了**(本仓反复修的
一类:选项在、能力不在)。修后的口径:透传 + 少了 `--full` 就在起跑前拒。

测试只跑**必然被拒**的组合,不会真的起面。
"""
import importlib.util
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_TOOL = os.path.join(_REPO, "tools", "serve_all.py")
_PY = sys.executable

_spec = importlib.util.spec_from_file_location("serve_all_tool", _TOOL)
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)


def _live(**kw):
    return sa.faces(**dict({"case": "case01", "full": True, "host": "127.0.0.1",
                            "hold": 1800}, **kw))[5010][0]


def test_真跑时种子与run_id和节点数都转到live_switch():
    cmd = _live(seed="20261015", run_id="demo1015-live", nodes=3)
    joined = " ".join(cmd)
    # 世界流的取值含 run_id,所以这两个必须**一起**能传进去才有 ③ 档对照
    assert "--seed 20261015" in joined and "--run-id demo1015-live" in joined
    assert "--nodes 3" in joined


def test_空值不拼进命令_免得live_switch收到裸参数():
    assert "--seed" not in _live(seed="  ", run_id="", nodes=0)
    assert "--nodes" not in _live(nodes=0)


def test_只翻记录那条分支不带种子():
    cmd = " ".join(_live(full=False, seed="20261015", run_id="demo1015-live"))
    assert "--review-only" in cmd and "--seed" not in cmd


def test_少了full时入口直接拒_不起面():
    for argv in (["--seed", "20261015"], ["--case", "case00", "--full", "--seed", "1"]):
        r = subprocess.run([_PY, "-X", "utf8", _TOOL] + argv,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 2, (argv, r.stdout, r.stderr)
        assert "只有 case01 真跑推演时才有消费者" in (r.stdout + r.stderr), argv
