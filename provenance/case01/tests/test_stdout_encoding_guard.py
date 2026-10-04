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
from case01.safestream import run_text, tolerant_stdout, utf8_env  # noqa: E402
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


def _env(io_encoding):
    """够用的最小子进程环境:编码口径由参数钉死,跨平台都能复现同一条结论。"""
    return {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONIOENCODING": io_encoding, "PYTHONPATH": REPO}


def _child(tmp_path, name, code, io_encoding):
    """跑子进程,stdout 落进文件;用 PYTHONIOENCODING 强制 locale,跨平台可复现。"""
    log = tmp_path / name
    env = _env(io_encoding)
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


# ---------------------------------------------------------------------------
# 5. 反方向咬人:`-X utf8` 下取外部命令输出不能交回 None
# ---------------------------------------------------------------------------
def test_run_text_survives_gbk_bytes_where_strict_decode_breaks(tmp_path):
    """先复现旧写法失效,再证明 run_text 拿到字符串。

    子进程按 GBK 写中文 → 父进程若用 `text=True` + 严格 UTF-8 解码,两边都坏,
    只是坏法不同:
    - Windows:`communicate()` 用读取线程收字节,线程里抛 UnicodeDecodeError
      被静默吞掉,`communicate()` 交回 **None**;`re.search(pat, None)` 是
      TypeError,`(proc.stdout or "")` 则把诊断输出静默吞光(live_switch 老坑)。
    - Linux:走 select 路径没有读取线程,解码异常直接从 `_translate_newlines`
      抛给调用方 —— 拿到的是崩溃而不是 None。

    所以控制组只断言"严格解码一定出事",平台各按各的坏法验;真正的契约
    (run_text 永远返回 str、不抛、保留 returncode)两边共用同一条断言。
    复现放在**孙进程**里做:否则那条异常会被 pytest 的钩子抓成警告或直接把
    测试打挂,噪音盖过结论(异常本身正是我们要的证据)。
    """
    probe = tmp_path / "probe_naive.py"
    probe.write_text(
        "import os, subprocess, sys\n"
        "inner = dict(os.environ); inner['PYTHONIOENCODING'] = 'gbk'\n"
        "p = subprocess.run([sys.executable, '-c', \"print('中文表头')\"],\n"
        "                   capture_output=True, text=True, encoding='utf-8',\n"
        "                   errors='strict', env=inner)\n"
        "print('STDOUT_TYPE=' + type(p.stdout).__name__)\n",
        encoding="utf-8")
    naive = run_text([sys.executable, str(probe)], env=_env("utf-8"))
    if sys.platform.startswith("win"):
        assert "STDOUT_TYPE=NoneType" in naive.stdout, \
            "复现失败:Windows 控制组必须给出 None,否则守卫白加\n" + naive.stderr[-400:]
    else:
        assert naive.returncode != 0, \
            "复现失败:Linux 严格解码必须抛,否则守卫白加\n" + naive.stdout[-400:]
        assert "UnicodeDecodeError" in naive.stderr, naive.stderr[-400:]

    got = run_text([sys.executable, "-c", "print('中文表头')"], env=_env("gbk"))
    assert isinstance(got.stdout, str) and got.stdout, "必须是字符串且非空"
    assert got.returncode == 0


def test_run_text_with_utf8_env_round_trips_chinese():
    """映射子进程配上 utf8_env 后:中文与 ▶ 原样回到看护进程。"""
    got = run_text([sys.executable, "-c", "print('{}')".format(UNGBK)],
                   env=utf8_env())
    assert UNGBK in got.stdout, got.stderr[-300:]


def test_utf8_env_pins_key_without_touching_os_environ():
    before = os.environ.get("PYTHONIOENCODING", "<unset>")
    env = utf8_env()
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert os.environ.get("PYTHONIOENCODING", "<unset>") == before
    assert utf8_env({"A": "1"})["A"] == "1", "给定的 base 必须被继承"


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="netstat 是 Windows 路径")
def test_port_listening_returns_bool_instead_of_raising():
    """看护进程的端口探活:取不到也只能给 bool,不能 TypeError。"""
    from case01.tools import map_after_run

    assert map_after_run._port_listening(59999) in (True, False)


# ---------------------------------------------------------------------------
# 6. 接线守卫:同族调用点必须都走同一份实现(两处一漂移就是又一批假数据)
# ---------------------------------------------------------------------------
def test_capture_call_sites_use_the_shared_helper():
    from case01.tools import map_after_run
    from case01.vizkit import live_run

    for fn, name in ((map_after_run._port_listening, "map_after_run._port_listening"),
                     (map_after_run.run_mapping, "map_after_run.run_mapping"),
                     (live_run._map_run, "live_run._map_run")):
        src = inspect.getsource(fn)
        assert "run_text(" in src, \
            "{} 必须用 safestream.run_text 取外部输出".format(name)
        assert "subprocess.run(" not in src, \
            "{} 不该再自己裸调 subprocess.run(严格解码会把输出变成 None)".format(name)


def test_all_cli_entrypoints_install_the_stdout_guard():
    from case01.tools import map_after_run
    from case01.vizkit import live_run

    for fn, name in ((run_cli.main, "case01.run"),
                     (batch_run.main, "batch_run"),
                     (map_after_run.main, "map_after_run"),
                     (live_run.main, "live_run")):
        assert "tolerant_stdout()" in inspect.getsource(fn), \
            "{} 入口必须挂 stdout 编码守卫".format(name)
