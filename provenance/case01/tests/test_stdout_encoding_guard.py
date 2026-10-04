# -*- coding: utf-8 -*-
"""子进程 stdout 编码守卫(2026-10-04,批次 261004-123726-h120/-035 事故)。

事故本身:batch_run 把子进程 `python -m case01.run` 的 stdout 重定向进 `.log`,
子进程按 Windows locale(GBK)编码文本流。模型答案里出现 `▶`(U+25B6)时
`orchestrator.run_case01` 的 `log(answer + "\n")` 抛 `UnicodeEncodeError`,
一条已经跑了 14 分钟的 run 死在 T0,台账只能记 attempt=2。同一条日志的 retry
段还能看到整片中文字符变成乱码 —— 因为父进程按 utf-8 打开文件、子进程按 GBK
写,`_failure_tail` 再按 utf-8 读回来只能靠 errors=replace 兜底。

两个缺陷,两道守卫:
1. 显示失败不该决定一次跑的生死 → CLI 入口挂 `tolerant_stdout()`(编不了就 `?`);
2. 日志字节必须和读写口径一致 → batch_run 给子进程钉 `PYTHONIOENCODING=utf-8`。
"""
import inspect
import io
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01 import run as run_cli  # noqa: E402
from case01.safestream import tolerant_stdout  # noqa: E402
from case01.tools import batch_run  # noqa: E402

# 模型真吐过的字符:台账里那条 run 就是死在它上面
UNGBK = "\u25b6 分批建仓 ▶"


def _gbk_stream():
    """造一个按 GBK 编码的可写文本流(等价于子进程在中文 Windows 上的 stdout)。"""
    buf = io.BytesIO()
    return io.TextIOWrapper(buf, encoding="gbk", line_buffering=True), buf


# ---------------------------------------------------------------------------
# 1. 复现:GBK 流上打印 ▶ 必须抛(守卫不是为假想场景加的)
# ---------------------------------------------------------------------------
def test_gbk_stream_without_guard_still_crashes():
    stream, _ = _gbk_stream()
    with pytest.raises(UnicodeEncodeError):
        print(UNGBK, file=stream)


def test_tolerant_stdout_survives_unencodable_char():
    """守卫后:同一份内容打印不抛,GBK 能编的字原样落,编不了的降级成 ?。"""
    stream, buf = _gbk_stream()
    tolerant_stdout(stream)
    print(UNGBK, file=stream)
    stream.flush()
    raw = buf.getvalue()
    assert "分批建仓".encode("gbk") in raw, "中文主体不该被替换掉"
    assert b"?" in raw, "▶ 必须降级成占位符而不是丢整行"
    assert raw.decode("gbk", "replace").replace("?", "") != "", "日志不能整段变空"


def test_tolerant_stdout_returns_same_stream_and_tolerates_fakes():
    """改不动的流(pytest capture / 无 reconfigure 的替身)只能原样退回,不能换对象。"""
    stream, _ = _gbk_stream()
    assert tolerant_stdout(stream) is stream
    plain = io.StringIO("x")                       # StringIO 没有 reconfigure
    assert tolerant_stdout(plain) is plain         # 不该抛,也不该包装
    assert tolerant_stdout() is sys.stdout         # 缺省打在真 stdout 上


# ---------------------------------------------------------------------------
# 2. 口径一致:batch_run 给子进程钉 utf-8
# ---------------------------------------------------------------------------
def _ns(**kw):
    base = {"model": "qwen3:8b", "embed_model": "", "disable_thinking": True}
    base.update(kw)
    return SimpleNamespace(**base)


def test_child_env_pins_utf8_for_log_bytes():
    env = batch_run._child_env(_ns())
    assert env["PYTHONIOENCODING"] == "utf-8", \
        "日志文件按 utf-8 打开,子进程 stdout 就必须按 utf-8 写"
    assert env["CASE01_LLM_MODEL"] == "qwen3:8b"
    assert env["CASE01_LLM_DISABLE_THINKING"] == "1"


def test_child_env_keeps_existing_knobs():
    """改造 env 的那几栏口径不能因为加编码守卫而漂移。"""
    env = batch_run._child_env(_ns(model="", disable_thinking=False))
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert "CASE01_LLM_MODEL" not in env, "model 为空时不该写这一栏"
    assert "CASE01_LLM_DISABLE_THINKING" not in env, "思考不关闭时必须 pop"


def test_child_env_does_not_touch_os_environ():
    before = os.environ.get("PYTHONIOENCODING", "<unset>")
    env = batch_run._child_env(_ns())
    env["PYTHONIOENCODING"] = "cp437"
    assert os.environ.get("PYTHONIOENCODING", "<unset>") == before, \
        "改的是副本;写脏 os.environ 会带走同进程其它 lane"


# ---------------------------------------------------------------------------
# 3. 接线守卫:守卫必须挂在入口上(事故正是"入口没挂"时发生的)
# ---------------------------------------------------------------------------
def test_cli_entrypoints_install_the_guard():
    for fn, name in ((run_cli.main, "case01.run"),
                     (batch_run.main, "batch_run")):
        src = inspect.getsource(fn)
        assert "tolerant_stdout()" in src, \
            "{} 入口必须挂 stdout 编码守卫,否则子进程一遇 ▶ 就废一条 run".format(name)


# ---------------------------------------------------------------------------
# 4. 端到端:重定向到文件的真实子进程(和 batch_run 完全一样的姿势)
# ---------------------------------------------------------------------------
REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))          # .../provenance
_PROTECT = "from case01.safestream import tolerant_stdout; tolerant_stdout(); "


def _child(tmp_path, name, code, io_encoding):
    """跑子进程,stdout 落进文件;用 PYTHONIOENCODING 强制 locale,跨平台可复现。"""
    log = tmp_path / name
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
           "PATH": os.environ.get("PATH", ""),
           "PYTHONIOENCODING": io_encoding, "PYTHONPATH": REPO}
    with open(log, "wb") as fh:
        rc = subprocess.call([sys.executable, "-c", code], stdout=fh,
                             stderr=subprocess.STDOUT, env=env, cwd=str(tmp_path))
    return rc, log.read_bytes()


def test_unguarded_gbk_child_reproduces_the_incident(tmp_path):
    """没守卫 + GBK 口径:一条 ▶ 就把 run 打死(这是事故的最小复现)。"""
    rc, raw = _child(tmp_path, "noguard.log", "print('{}')".format(UNGBK), "gbk")
    assert rc != 0, "复现失败就该改测试,别改产品代码:这条命令必须崩"
    assert b"UnicodeEncodeError" in raw


def test_guarded_gbk_child_survives(tmp_path):
    """有守卫 + GBK 口径:编不了的字降级,run 继续跑完。"""
    rc, raw = _child(tmp_path, "gbk.log", _PROTECT + "print('{}')".format(UNGBK),
                    "gbk")
    assert rc == 0, raw.decode("gbk", "replace")[-500:]
    text = raw.decode("gbk", "replace")
    assert "分批建仓" in text and "?" in text, "保住 run 的同时中文别丢"


def test_child_env_utf8_lands_the_char_intact(tmp_path):
    """batch_run 钉了 utf-8 之后:▶ 原样进日志,既不崩也不变 ?。"""
    rc, raw = _child(tmp_path, "utf8.log", _PROTECT + "print('{}')".format(UNGBK),
                    "utf-8")
    assert rc == 0, raw.decode("utf-8", "replace")[-500:]
    assert UNGBK in raw.decode("utf-8")
