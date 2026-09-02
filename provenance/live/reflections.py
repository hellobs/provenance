"""反思(thought)标记存储:人机协同闭环的持久化层。

- 反思文本来源:运行中 agent 的记忆流(associate.retrieve_thoughts 的 Concept 节点),
  文本只在运行期存在(checkpoint 快照仅存 node_id 引用),因此**标记时必须把文本
  一并存档**,否则事后无法审计。
- 存储:results/checkpoints/reflection_marks.json(数组,与 interventions.json 同域)。
- 导出:JSONL(每行一个样本),供 LoRA 线(罗昊哲)消费为 (反思, 专家纠正) 训练对。
"""
import datetime
import json
import os

from live.state import checkpoint_file, log, read_json

MARKS_PATH = None  # 延迟解析(state.BASE_DIR 运行期不变,首次调用取)


def marks_path() -> str:
    global MARKS_PATH
    if MARKS_PATH is None:
        MARKS_PATH = checkpoint_file("reflection_marks.json")
    return MARKS_PATH


VALID_VERDICTS = ("correct", "incorrect", "partial")


def load_marks() -> list:
    return read_json(marks_path(), default=[]) or []


def append_mark(record: dict) -> None:
    path = marks_path()
    marks = load_marks()
    marks.append(record)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(marks, f, ensure_ascii=False, indent=2)


def rebuild_jsonl() -> str:
    """从 marks 重建 JSONL 导出文件,返回文件路径。

    每行一个 LoRA 训练样本(人机协同闭环的 B 线数据格式):
    {"agent", "simulation", "sim_time", "thought", "verdict", "correction", "marked_time"}
    """
    path = marks_path()
    marks = load_marks()
    out = path.replace(".json", ".jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for m in marks:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    return out


def marked_node_ids() -> set:
    """已标记的 node_id 集合(用于前端区分 pending/marked)。"""
    return {str(m.get("node_id", "")) for m in load_marks() if m.get("node_id")}


def new_mark(agent: str, simulation: str, sim_time: str, node_id: str,
             thought: str, verdict: str, correction: str) -> dict:
    return {
        "agent": agent,
        "simulation": simulation,
        "sim_time": sim_time,
        "node_id": node_id,
        "thought": thought,
        "verdict": verdict,          # correct / incorrect / partial
        "correction": correction,    # 专家纠正文本(incorrect/partial 时非空)
        "marked_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operator": "expert",
    }
