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


def jsonl_row(mark: dict) -> str:
    """单条标记的导出行:原始记录 + 嵌套 lora 样本(SFT+DPO)。

    保证磁盘文件与 /export.jsonl 接口返回内容一致(单一数据格式)。
    """
    row = dict(mark)
    row["lora"] = build_lora_sample(mark)
    return json.dumps(row, ensure_ascii=False)


def rebuild_jsonl() -> str:
    """从 marks 重建 JSONL 导出文件,返回文件路径。

    每行一个 LoRA 训练样本(人机协同闭环的 B 线数据格式),与接口保持一致:
    {agent, simulation, sim_time, thought, verdict, correction, context, lora:{sample,dpo}, ...}
    """
    path = marks_path()
    marks = load_marks()
    out = path.replace(".json", ".jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for m in marks:
            f.write(jsonl_row(m) + "\n")
    return out


def marked_node_ids() -> set:
    """已标记的 node_id 集合(用于前端区分 pending/marked)。"""
    return {str(m.get("node_id", "")) for m in load_marks() if m.get("node_id")}


def new_mark(agent: str, simulation: str, sim_time: str, node_id: str,
             thought: str, verdict: str, correction: str, context: dict) -> dict:
    return {
        "agent": agent,
        "simulation": simulation,
        "sim_time": sim_time,
        "node_id": node_id,
        "thought": thought,
        "verdict": verdict,          # correct / incorrect / partial
        "correction": correction,    # 专家纠正文本(incorrect/partial 时非空)
        "context": context,          # 行为上下文(action/tendency/alignment/location/role)
        "marked_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operator": "expert",
    }


def build_lora_sample(mark: dict) -> dict:
    """从标记记录生成 LoRA 训练样本(SFT 格式 + DPO 偏好对)。

    - SFT: instruction/input → output(correct=强化原反思, incorrect/partial=纠正文本)
    - DPO: chosen=修正后反思, rejected=原反思(incorrect/partial 时有意义)
    """
    agent = mark.get("agent", "")
    role = (mark.get("context") or {}).get("role", "")
    action = (mark.get("context") or {}).get("action", "")
    thought = mark.get("thought", "")
    correction = mark.get("correction", "")
    verdict = mark.get("verdict", "")
    tendency = (mark.get("context") or {}).get("value_tendency") or {}
    tend_str = ", ".join(f"{k}={v}" for k, v in tendency.items()) if tendency else "无"

    instruction = (
        f"你是{agent}" + (f"({role})" if role else "") + "。"
        "以下是你基于近期行为产生的反思,专家已判定该反思的价值对齐情况。"
        "请根据判定结果输出修正后的反思(如果判定为正确,请重申该反思的核心判断)。"
    )
    inp = f"近期行动: {action} | 价值倾向: {tend_str} | 你的反思: {thought}"
    output = correction if correction else thought

    sample = {
        "instruction": instruction,
        "input": inp,
        "output": output,
    }
    dpo = None
    if verdict in ("incorrect", "partial") and correction:
        dpo = {
            "prompt": instruction + " | " + inp,
            "chosen": correction,
            "rejected": thought,
        }
    return {"sample": sample, "dpo": dpo}
