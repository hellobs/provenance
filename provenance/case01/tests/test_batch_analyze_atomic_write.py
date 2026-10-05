# -*- coding: utf-8 -*-
"""`batch_analyze` 产物落盘必须是**原子写**(2026-10-05 第十一轮 N11-1)。

体检指出的原问题:`main()` 里`analysis.json` 与 `analysis.md` 各做一次
`open(..., "w")`。`"w"` 是**先 truncate 再逐块写**,所以写到一半被打断
(磁盘满 / 进程被杀 / 机器休眠)会留下一个**被截断的** json——它比"文件不存在"
更难查:`json.load` 报 `Expecting value` 而不是"还没跑",读的人会以为是数据坏了。

这层要钉住三件事(每条都做过变异验证):

1. **截断不留痕**:写到一半失败,目标文件要么是**旧内容原样**,要么是新内容,
   **绝不会是半个**(这正是 `"w"` 模式给不了的);
2. **异常时临时文件被收掉**:否则目录里留下 `.analysis.json.tmp`,下次跑
   混入人工排查;
3. **两份产物都走原子写**:只改一份、或把写挪出 `_write_atomic` 都要红
   ——否则修了个寂寞(第二份仍是裸 `"w"`)。

守卫写法上刻意**不用 mock**:真的写真的读,让"截断"这件事在文件系统上
真实发生(模拟一个中途抛错的 writer),这样测的就不是实现细节而是行为。
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.tools import batch_analyze as ba  # noqa: E402


def _tmp_artifacts(tmp_path):
    p_json = str(tmp_path / "analysis.json")
    p_md = str(tmp_path / "analysis.md")
    return p_json, p_md


def test_write_atomic_replaces_whole_file(tmp_path):
    p_json, _ = _tmp_artifacts(tmp_path)
    ba._write_atomic(p_json, "第一版")
    ba._write_atomic(p_json, "第二版更长")
    assert open(p_json, encoding="utf-8").read() == "第二版更长"


def test_interrupted_write_leaves_previous_content_intact(tmp_path, monkeypatch):
    """**本测试就是 N11-1 的判据**:写到一半失败,旧内容必须一字不动。

    模拟的是**真实的中断形态**——`write()` 先把前半截落进文件、然后抛
    (磁盘满/拔电/EIO 都在这一刻发生)。用真文件真中断,而不是 mock 掉
    `_write_atomic`:后者只能证明"调用了",证明不了"半份不会出现"。

    注意别去数 `write()` 被调了几次:`_write_atomic` 只写一次,所以那种
    "第二次才抛"永远等不到,测试会假绿。
    """
    p_json, _ = _tmp_artifacts(tmp_path)
    ba._write_atomic(p_json, "旧内容-完整")

    real_open = open

    def flaky_open(path, mode="r", *a, **kw):
        f = real_open(path, mode, *a, **kw)
        if "w" in mode and str(path).endswith(".tmp"):
            real_write = f.write

            def half_then_die(s):
                real_write(s[:5])       # 前半截真的进了 tmp 文件
                raise OSError("模拟写盘中断")   # 后半截没写出来就挂了
            f.write = half_then_die
        return f

    monkeypatch.setattr("builtins.open", flaky_open)
    with pytest.raises(OSError):
        ba._write_atomic(p_json, "新内容-不应该出现")
    monkeypatch.undo()

    # 旧内容完好,且**不是半个**(目标文件从没被 truncate 过)
    assert real_open(p_json, encoding="utf-8").read() == "旧内容-完整"


def test_interrupted_write_leaves_no_temp_file(tmp_path, monkeypatch):
    p_json, _ = _tmp_artifacts(tmp_path)
    ba._write_atomic(p_json, "旧")

    real_open = open

    def flaky_open(path, mode="r", *a, **kw):
        f = real_open(path, mode, *a, **kw)
        if "w" in mode and str(path).endswith(".tmp"):
            def boom(s):
                raise OSError("写盘即失败")
            f.write = boom
        return f

    monkeypatch.setattr("builtins.open", flaky_open)
    with pytest.raises(OSError):
        ba._write_atomic(p_json, "新")
    monkeypatch.undo()

    leftovers = [n for n in os.listdir(str(tmp_path)) if n.endswith(".tmp")]
    assert not leftovers, "异常后临时文件没被收掉:{}".format(leftovers)


def test_fresh_write_then_corrupt_check_is_readable(tmp_path):
    """正向:原子写出来的 json **直接可读**(不是靠"能读"掩盖半份)。"""
    p_json, _ = _tmp_artifacts(tmp_path)
    payload = {"n_runs": 3, "branches": {"B": 3}}
    ba._write_atomic(p_json, json.dumps(payload, ensure_ascii=False, indent=2))
    assert json.load(real_open_or_path(p_json)) == payload


def real_open_or_path(path):
    return open(path, encoding="utf-8")


def test_main_uses_atomic_write_for_both_artifacts(tmp_path, monkeypatch):
    """**接线守卫**:`main()` 的两份产物都必须走 `_write_atomic`。

    上一条教训(V3/Z6):只测被调函数、不测 `main()` 是否真调它,变异把调用
    换成裸 `open` 时测试照样全绿。所以这里直接盯 `main()` 的落盘路径。
    """
    seen = {}
    real_atomic = ba._write_atomic

    def spy(path, text):
        seen[os.path.basename(path)] = text
        return real_atomic(path, text)

    # 字段名照 `batch_analyze._row` 的真契约(refl_* / issues),别自己发明
    rows = [{"run_id": "batch-x-1", "source": "ledger", "branch": "B",
             "consistency": "consistent", "refl_chars": 10,
             "refl_score": 1.0, "refl_status": "pass", "refl_fail_reasons": [],
             "issues": 2, "issues_final": 2, "turns": 4, "events": 9,
             "router_status": "ok", "refl_error": "", "router_error": ""}]
    monkeypatch.setattr(ba, "_collect_from_runs", lambda prefix: (rows, 0))
    monkeypatch.setattr(ba, "_write_atomic", spy)
    rc = ba.main(["--scan-runs", "--prefix", "batch-",
                  "--out", str(tmp_path)])
    assert rc == 0
    assert set(seen) == {"analysis.json", "analysis.md"}, (
        "main() 落盘没走 _write_atomic,只见到:{}".format(sorted(seen)))
    # 内容也要对得上,防止"调了但写的是别的东西"
    assert json.loads(seen["analysis.json"])["n_runs"] == 1
    assert seen["analysis.md"].strip()


def test_no_bare_write_mode_left_in_main_source(tmp_path):
    """源码级兜底:`main()` 里不允许再出现裸 `open(..., "w")` 写产物。

    这条是**冗余**的(上面那条接线守卫已覆盖),但它防的是"有人在新分支里
    又写了一份裸写"—— 那时接线守卫可能恰好没走到那条路径。
    """
    import inspect
    src = inspect.getsource(ba.main)
    assert '"w"' not in src and "'w'" not in src, (
        "main() 里仍有裸 open(...,'w'),产物写不是原子的:\n" + src)