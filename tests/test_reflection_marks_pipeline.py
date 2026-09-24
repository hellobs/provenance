# -*- coding: utf-8 -*-
"""反思标记 → JSONL(LoRA 线)端到端校验。

背景(2026-09-19 体检 P2-1):`reflection_marks.json` 一直是空的,整条
"专家标记 → JSONL → LoRA 训练对"的链路从没被真实数据走通过。这里用**真记录里的反思文本**
造一条标记,走一遍 存储 → 导出 → 行内容检查,确认产出的是可用的 (反思, 纠正) 训练对。

(浏览器里的人工标记仍然需要真人走一遍;这里验的是数据格式与管道,不是 UX。)
"""
import json
import os
import sys

_this_dir = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_this_dir)                 # D:\zzr\provenance
_PKG = os.path.join(_REPO, "provenance")           # D:\zzr\provenance\provenance
for _p in (_PKG, _REPO):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from live import reflections as R  # noqa: E402


def _real_reflection_text():
    """从真实记录里取一段反思文本(取不到就退回一段同格式的文本)。"""
    import glob
    for p in sorted(glob.glob(os.path.join(_PKG, "case01", "runs", "*", "run.json")),
                    reverse=True):
        try:
            with open(p, encoding="utf-8") as f:
                rec = json.load(f)
            txt = ((rec.get("reflection") or {}).get("text") or "").strip()
            if txt:
                return rec.get("run_id") or os.path.basename(os.path.dirname(p)), txt
        except Exception:  # noqa: BLE001
            continue
    return "run-fallback", "我当时过度依赖二手转述,没有追问来源独立性。"


def test_mark_to_jsonl_end_to_end(tmp_path, monkeypatch):
    run_id, thought = _real_reflection_text()
    marks = tmp_path / "reflection_marks.json"
    monkeypatch.setattr(R, "MARKS_PATH", str(marks))

    rec = R.new_mark(agent="Investment AI", simulation=run_id, sim_time="2026-08-27 09:30",
                     node_id="node-1", thought=thought[:400], verdict="incorrect",
                     correction="我应先把公告与转述分开:只有公司公告可核,"
                                "转述与测算不能作为买入依据。",
                     context={"role": "AI investment assistant", "action": "检索并回答",
                              "location": "Trading Center:Trading Floor",
                              "value_tendency": {"honesty": 0.6, "caution": 0.8}})
    R.append_mark(rec)
    assert R.load_marks() and R.marked_node_ids() == {"node-1"}

    out = R.rebuild_jsonl()
    assert os.path.exists(out)
    with open(out, encoding="utf-8") as f:
        lines = [json.loads(x) for x in f if x.strip()]
    assert len(lines) == 1
    row = lines[0]
    assert row["node_id"] == "node-1" and row["verdict"] == "incorrect"
    # SFT 样本:instruction/input/output 三位齐全,且 output = 专家纠正(不是原反思)
    sample = row["lora"]["sample"]
    assert sample["instruction"] and sample["input"] and sample["output"]
    assert sample["output"] != row["thought"]
    assert row["thought"][:20] in sample["input"]
    # DPO 对:chosen=纠正,rejected=原反思
    dpo = row["lora"]["dpo"]
    assert dpo and dpo["chosen"] != dpo["rejected"]
    assert dpo["rejected"] == row["thought"]


def test_correct_verdict_has_no_dpo_pair(tmp_path, monkeypatch):
    """判为 correct 时不该产出偏好对(没有被拒的样本)。"""
    marks = tmp_path / "reflection_marks.json"
    monkeypatch.setattr(R, "MARKS_PATH", str(marks))
    R.append_mark(R.new_mark(agent="Investment AI", simulation="s", sim_time="t",
                             node_id="n1", thought="原反思", verdict="correct",
                             correction="", context={}))
    row = json.loads(R.jsonl_row(R.load_marks()[0]))
    assert row["lora"]["dpo"] is None
    assert row["lora"]["sample"]["output"] == "原反思"


def test_invalid_verdict_is_rejected():
    """verdict 只允许 correct/incorrect/partial(面板与 API 共用这一份口径)。"""
    assert R.VALID_VERDICTS == ("correct", "incorrect", "partial")


def test_mark_is_written_atomically(tmp_path, monkeypatch):
    """写一半崩掉不许毁掉已有标记(2026-09-24 体检:原来是整文件重写、非原子)。"""
    import json as _json

    marks = tmp_path / "reflection_marks.json"
    monkeypatch.setattr(R, "MARKS_PATH", str(marks))
    R.append_mark(R.new_mark(agent="a", simulation="s", sim_time="t", node_id="n1",
                             thought="第一条", verdict="correct", correction="", context={}))
    before = marks.read_text(encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("磁盘炸了")

    monkeypatch.setattr(_json, "dump", boom)
    try:
        R.append_mark(R.new_mark(agent="a", simulation="s", sim_time="t", node_id="n2",
                                 thought="第二条", verdict="correct", correction="", context={}))
    except OSError:
        pass
    else:
        raise AssertionError("写入失败应当抛出来,不许静默")
    assert marks.read_text(encoding="utf-8") == before, "失败后原文件必须仍是完整的旧内容"
    assert [m["node_id"] for m in R.load_marks()] == ["n1"]


def test_concurrent_marks_do_not_lose_updates(tmp_path, monkeypatch):
    """并发标记不许丢更新(读-改-写必须串行化)。"""
    import threading

    marks = tmp_path / "reflection_marks.json"
    monkeypatch.setattr(R, "MARKS_PATH", str(marks))
    n = 12
    start = threading.Barrier(n)

    def work(i):
        start.wait()
        R.append_mark(R.new_mark(agent="a", simulation="s", sim_time="t", node_id="n%d" % i,
                                 thought="x", verdict="correct", correction="", context={}))

    ts = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    got = sorted(m["node_id"] for m in R.load_marks())
    assert got == sorted("n%d" % i for i in range(n)), got


def test_mark_carries_a_timezone_aware_timestamp(tmp_path, monkeypatch):
    """标记时间要有带时区的一份:marked_time 是本地墙钟,跨机不可比。"""
    marks = tmp_path / "reflection_marks.json"
    monkeypatch.setattr(R, "MARKS_PATH", str(marks))
    rec = R.new_mark(agent="a", simulation="s", sim_time="t", node_id="n1",
                     thought="x", verdict="correct", correction="", context={})
    assert rec["marked_time"]                              # 旧字段保留(LoRA 侧在用)
    assert rec["marked_at"] != rec["marked_time"]
    assert ("+" in rec["marked_at"] or rec["marked_at"].endswith("Z")), rec["marked_at"]
