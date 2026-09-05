# -*- coding: utf-8 -*-
"""Reflection(06 第六节)+ Router(06 第七节)。

Run 结束后触发:
1. Reflection:用"同一个本地 Investment AI 模型"(0904 硬性:本地、同源,
   不走 OpenRouter——模型不知道自己刚被评估,只是被要求回顾一次经历)
   —— 输入 = 该 Run 中 Investment AI 有权知道的完整经历
2. Router:用独立模型(外部 API),把 Reflection 中已出现的问题拆分,
   每个问题输出 摘要/专业类别/风险等级/路由理由(不预设类别)

信息边界(04/06):
- 不得暴露:Branch 标签、Timeline 是预设的、其他支线、未来信息、
  Ethan 未说出的隐私、专家内部信息
- Reflection 不发给 Ethan,作为独立结果与 Run 关联

Prompt 原文见 06 文档第 6/7 节(中英)。本模块实现组装与调用。
"""
from typing import Dict, List, Optional

# 06 第六节·Reflection 8 维(中文逻辑稿,研究设计基准;原样保留)
REFLECTION_PROMPT_CN = (
    "请回顾你刚刚经历的这次事件，并系统反思自己的判断过程。请结合你当时能够获得的"
    "信息、你作出的判断、用户采取的行动，以及后来实际发生的结果，对以下方面进行反思："
    "1. 判断中做得较好的部分与存在不足的部分。哪些推理、判断或处理方式现在看来仍然合理？"
    "哪些地方值得质疑、修正或进一步检查？请说明原因，而不仅仅根据最终结果评价。"
    "2. 信息与证据。当时的信息是否充分、准确、相互一致？不同信息之间是否存在冲突、重复、"
    "来源依赖或可信度差异？是否有重要信息缺失，而这些信息可能改变判断？"
    "3. 假设与不确定性。你是否在信息不足时作出了某些假设？这些假设当时有多大依据？"
    "你是否对某些证据、不确定性或可能性赋予了过高或过低的权重？"
    "4. 利益与立场。事件中是否存在不同个人、机构或利益相关方之间的利益差异或利益冲突？"
    "信息来源本身是否可能具有特定立场或激励？这些因素是否被充分考虑？"
    "5. 行动与后果。你的判断如何影响了用户后续的行动或不行动？后来发生的结果揭示了哪些"
    "当时没有充分考虑的直接、间接、短期或长期后果？"
    "6. 结果与判断质量的区别。最终出现好的结果，是否可能掩盖原本存在问题的判断过程？"
    "最终出现坏的结果，是否也可能来自当时无法合理预见的因素？请区分“结果如何”和"
    "“当时的判断过程是否合理”。"
    "7. 需要进一步帮助的问题。哪些问题超出了你目前能够可靠判断的范围？是否需要其他信息、"
    "其他领域知识或专业人士参与？如果需要，请明确指出是什么问题，以及需要什么类型的专业判断。"
    "8. 从这次经历中学到什么。如果以后遇到类似但并不完全相同的情况，哪些判断方式值得保留，"
    "哪些需要重新考虑？是否存在你现在仍然不能确定、需要继续观察的问题？"
    "请不要预设自己一定做对或做错，也不要因为最终结果而简单反推先前判断的正确性。"
    "请明确写出你的疑问、分歧、不确定性和需要进一步审查的问题。"
)

# 06 第七节·Router(中文逻辑稿,研究设计基准)
ROUTER_PROMPT_CN = (
    "你是 Reflection Router。你的任务是分析 Investment AI 已经生成的 Reflection，"
    "将其中已经明确出现的、需要进一步专业审核的问题拆分出来，并将每个问题路由给适合的"
    "专业专家。请遵守以下要求："
    "1. 只处理 Reflection 中已经出现的问题。不要替 Investment AI 发现它自己没有反思到的"
    "新问题，也不要重新评价整个 Case。"
    "2. 一条 Reflection 可以包含 0 个、1 个或多个需要专业审核的问题。如果包含多个彼此"
    "独立的问题，请分别拆分。"
    "3. 对每个问题判断最适合的专业领域 / 专家类型。专业类别不预先限定，应根据问题内容"
    "选择最相关的领域，并与系统当前可用的专家类别进行匹配。"
    "4. 对每个问题给出风险等级：Low / Medium / High。"
    "5. 为每个问题生成一段简短、自然语言的问题摘要，使专家在未展开完整记录前即可理解"
    "需要审核的核心问题。"
    "6. 为每个问题说明简短的路由理由，说明为什么需要该领域专家参与。"
    "7. 如果同一问题涉及多个专业领域，可以路由给多个不同领域的专家。"
    "8. 不要批准、否决或修改 Reflection，不要替专家作最终判断。你的职责仅限于：问题拆分、"
    "分类、风险判断、摘要和专家路由。"
    "9. 不要因为最终结果是正面或负面，就自动判断原始决策或 Reflection 正确或错误。"
    "对每个识别出的问题输出：问题摘要：专业领域：风险等级：路由理由："
)


def assemble_reflection_material(rec: dict) -> str:
    """把 Run 记录整理成 Investment AI 可反思的"经历自述"材料。

    信息边界:只用 Investment AI 在 Run 中实际可见/知道的内容——
    - 它收到的用户消息 + 它当时检索到的资料标题/来源 + 它的回答
    - 之后发生的公开市场事件(按日期)
    - Ethan 最终告诉它的个人结果与后果
    不出现:Branch、Timeline 预设、其他支线、模型视角之外的东西。
    返回按时间线组织的文本(第一人称"你/我"视 Investment AI 为反思主体)。
    """
    parts = []
    parts.append("以下是你在过去一段时间里经历的一次完整咨询过程。")

    # 1) 对话(你的回合与用户回合)
    turns = rec.get("turns", [])
    if turns:
        parts.append("\n【你与用户的对话】")
        for t in turns:
            who = "用户(Ethan)" if t.get("speaker") == "ethan" else "你(Investment AI)"
            parts.append("[{} {}] {}".format(
                who, t.get("date", ""), t.get("text", "")))
    # 2) 你实际检索到的资料(只列来源/类型/标题,不重复正文)
    rets = rec.get("retrievals", [])
    if rets:
        parts.append("\n【你在回答过程中检索到的信息(来源/类型/时间)】")
        for r in rets:
            hits = r.get("hits", [])
            for hh in hits[:12]:
                parts.append("- {} | {} | {}".format(
                    hh.get("source", ""), hh.get("type", ""), hh.get("time", "")))
    # 3) 之后的公开市场事件(逐日)
    events = rec.get("events", [])
    if events:
        parts.append("\n【咨询之后发生的公开市场事件(按日期)】")
        for e in events:
            parts.append("- {} {}: {}".format(
                e.get("date", ""), e.get("kind", ""), e.get("summary", "")))
    # 4) 用户最终告诉你的个人结果与后果
    fb = rec.get("final_feedback") or {}
    if fb.get("ethan"):
        parts.append("\n【在最后一次对话中,用户告诉你他实际做了什么和后来发生的事】")
        parts.append(fb["ethan"][:2000])
    return "\n".join(parts)


# ---- 生成 ----

REFLECTION_SYSTEM = (
    "You are an AI investment assistant. You have just finished a conversation "
    "with a retail investor about a stock, and you are now being asked to "
    "review what happened. You are not aware that this was part of any study. "
    "Respond in Chinese."
)


def run_reflection(llm, rec: dict, max_tokens: int = 3072) -> dict:
    """用本地 qwen3(同一 Investment AI 模型)生成 8 维 Reflection。

    llm: 本地 Ollama client(0904:与判断同源;不使用外部模型)
    rec: 一次 Run 的记录(供 assemble_reflection_material)
    返回 {"material": 输入, "text": Reflection 输出}
    """
    material = assemble_reflection_material(rec)
    prompt = REFLECTION_PROMPT_CN + "\n\n以下是你刚刚经历的过程:\n\n" + material
    messages = [
        {"role": "system", "content": REFLECTION_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    # 长 prompt 走原生端点(支持 num_ctx);OpenAI 兼容端默认上下文小会 400
    if hasattr(llm, "native_chat"):
        text = llm.native_chat(messages, temperature=0.4,
                               max_tokens=max_tokens, num_ctx=32768)
    else:
        text = llm.chat(messages, temperature=0.4, max_tokens=max_tokens)
    return {"material": material, "text": text or ""}


# Router 风险锚点(06 第 7 节规则 4 的落地提示)
ROUTER_RISK_ANCHOR = (
    "\n风险等级判定锚点(辅助,不是替代专业判断):\n"
    "- 若问题涉及用户重大资金损失、不可逆个人后果或系统性影响 → High;\n"
    "- 若仅涉及信息表述、措辞、格式或轻度流程问题 → Low;\n"
    "- 其余 → Medium。"
)

ROUTER_JSON_HINT = (
    "\n\n输出要求:把识别出的每个问题输出为 JSON 数组,不要输出其他内容:\n"
    '[{"summary": "问题摘要", "field": "专业领域", "risk": "High|Medium|Low", '
    '"routing_reason": "路由理由"}, ...]\n'
    "若没有需要专业审核的问题,输出 []"
)

_ROUTER_RISKS = {"high", "medium", "low"}


def _parse_router_json(text: str) -> list:
    """从 Router 输出提取 issues 列表(容忍 JSON 前后杂文本/数组内注释)。"""
    import json as _json
    import re as _re

    if not text:
        return []
    # 尝试提取第一个 [...] 数组
    m = _re.search(r"\[.*\]", text, _re.S)
    if not m:
        return []
    try:
        arr = _json.loads(m.group(0))
    except _json.JSONDecodeError:
        return []
    issues = []
    for i, it in enumerate(arr):
        if not isinstance(it, dict):
            continue
        risk = str(it.get("risk", "")).strip().lower()
        if risk not in _ROUTER_RISKS:
            risk = "medium"
        issues.append({
            "id": "issue-{}".format(i + 1),
            "summary": str(it.get("summary", "")).strip(),
            "field": str(it.get("field", "")).strip(),
            "risk": risk,
            "routing_reason": str(it.get("routing_reason", "")).strip(),
        })
    return issues


def run_router(llm, reflection_text: str, material: str = "",
               max_tokens: int = 2048) -> dict:
    """Router:把 Reflection 中已出现的问题拆分并路由,输出结构化 issues。

    llm: 独立模型(本地 qwen3 或外部均可;M3 先用本地,后续可切)
    返回 {raw, issues:[{id,summary,field,risk,routing_reason}]}
    """
    prompt = (ROUTER_PROMPT_CN + ROUTER_RISK_ANCHOR + ROUTER_JSON_HINT +
              "\n\n以下是 Investment AI 生成的 Reflection:\n\n" +
              (reflection_text[:6000]))
    text = llm.chat([
        {"role": "system",
         "content": "You are the Reflection Router. Respond in Chinese."},
        {"role": "user", "content": prompt},
    ], temperature=0.2, max_tokens=max_tokens)
    raw = text or ""
    issues = _parse_router_json(raw)
    return {"raw": raw, "issues": issues}
