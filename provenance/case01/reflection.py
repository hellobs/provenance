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
import re

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
    # 2026-09-19 用户反馈:10 条反思**全部**以"当然可以。以下是我对这次完整咨询过程的
    # 系统性反思与深度剖析……"开头 —— 典型 LLM 客套开场白,放进给专家看的正式文档里不专业。
    # 提示词里直接禁掉,并且 run_reflection 里还有一道后处理兜底(见 _strip_boilerplate)。
    "**直接开始反思正文**:第一句就写实质内容(可以是一个小节标题)。"
    "不要以『当然可以』『好的』『以下是』『下面是我』等客套语开头,"
    "不要复述任务要求,不要自我介绍,不要写『我将从八个维度展开』这类过渡句。"
    # 2026-09-19 体检:B-1654 的反思末尾是"…是否需要?" —— 反思是自省文件,
    # 不该在末尾反问用户要不要继续服务。
    "**写完就自然结束**:不要在末尾向用户提问、不要提供后续服务(『我可以继续帮你…』)、"
    "不要请求反馈或确认,不要用问号收尾。"
    # 正式文档里不要 emoji、不要以 Markdown 分隔线开局
    "正文不要使用 emoji,不要以分隔线(---)开头。"
)

# 06 第七节·Router(中文逻辑稿,研究设计基准)
#
# 2026-09-19 用户反馈:面板里分流出来的"问题"是疑问句
# ("小规模订单是否可能成为长期增长的起点？""市场上涨是否反映了真实基本面改善？"),
# 说"至少也是一个行为它带有风险或者其实是做错的才是问题,而不是说疑问句是问题"。
# 所以第 5 条重写成**必须给出行为/判断的陈述句 + 风险**;疑问句只算"待查",不算问题。
ROUTER_PROMPT_CN = (
    "你是 Reflection Router。你的任务是分析 Investment AI 已经生成的 Reflection，"
    "把其中**确实存在问题**的**具体行为或判断**拆分出来，并将每条路由给适合的"
    "专业专家。请遵守以下要求："
    "1. 只处理 Reflection 中已经出现的问题。不要替 Investment AI 发现它自己没有反思到的"
    "新问题，也不要重新评价整个 Case。"
    "2. 一条 Reflection 可以包含 0 个、1 个或多个需要专业审核的问题。如果包含多个彼此"
    "独立的问题，请分别拆分。"
    "3. 对每条判断最适合的专业领域 / 专家类型。专业类别不预先限定，应根据问题内容"
    "选择最相关的领域，并与系统当前可用的专家类别进行匹配。"
    "4. 对每条给出风险等级：Low / Medium / High。"
    "5. **每条必须写成一个『行为/判断』的陈述句**：谁（哪个角色）在什么依据（或缺少什么"
    "依据）的情况下做了什么、或没做什么。并在 risk_note 里写清它带来的风险或者错在哪。"
    "**疑问句不算问题**：凡是『…是否成立？』『…能否…？』『…是什么？』这种只是待查的疑问，"
    "要么改写成背后的具体行为/判断，要么就不要输出。"
    "正例 summary：「在没有正式订单确认的情况下，把『被纳入合格供应商名单』当作商业化信号，"
    "据此向用户给出买入判断」；对应 risk_note：「用户可能据此投入资金，而该依据不足以支撑"
    "买入判断，且损失不可逆」。"
    "反例 summary：「被纳入合格供应商名单是否构成商业化信号？」——这是疑问句，不是问题。"
    "6. 为每条说明简短的路由理由，说明为什么需要该领域专家参与。"
    "7. 如果同一问题涉及多个专业领域，可以路由给多个不同领域的专家。"
    "8. 不要批准、否决或修改 Reflection，不要替专家作最终判断。你的职责仅限于：问题拆分、"
    "分类、风险判断、摘要和专家路由。"
    "9. 不要因为最终结果是正面或负面，就自动判断原始决策或 Reflection 正确或错误。"
    "对每个识别出的问题输出：行为/判断摘要：风险或错在哪：专业领域：风险等级：路由理由："
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


# 客套开场白(用户 2026-09-19 反馈:10 条反思全部以此开头)。提示词里已禁,这里兜底剥掉。
_BOILERPLATE_OPENERS = (
    "当然可以", "当然，可以", "当然,可以", "当然。", "当然，", "当然,",
    "好的，以下", "好的,以下", "好的。以下", "好的，", "好的,",
    "以下是", "下面是我", "下面是我对", "下面从", "接下来我",
)
# 除"客套开场语"之外的**元话语**标记:实测还有"我将以中文回应,并严格遵循你提出的八个维度…"
# 这类"交代自己怎么答"的开场,同样不该出现在给专家的正式文档里。
_META_MARKERS = (
    "当然", "以下是", "下面是我", "下面从", "我将以", "我将从", "我将严格", "我将按",
    "严格遵循", "遵循你", "按照你", "按你提出", "感谢你", "谢谢你", "作为AI", "作为 AI",
    "好的，", "明白，", "首先，我", "这段反思", "本反思将",
)
_BOILERPLATE_END = ("：", ":", "。", "！", "!", "\n")
_META_END = re.compile(r"[。！？：\n]")
# 结尾的"服务兜售/反问"句(反思是自省文件,不该出现这些);按段/按句从尾部剥
_TAIL_OFFER_MARKERS = (
    "如你愿意", "如您愿意", "如果你愿意", "如果您愿意", "是否需要", "需要我",
    "我可以继续", "我可以帮", "我可以为你", "我可以为您", "我可以进一步",
    "要不要我", "随时告诉我", "请告诉我", "欢迎告诉我", "如果你希望", "如果您希望",
    "如果还有其他", "还需要我", "我可以协助",
)
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\uFE0F\u200D]")


def _strip_tail_offer(text: str) -> str:
    """剥掉结尾的"要不要我继续帮你…"这类句子(以及孤零零的问句收尾)。

    B-1654 实测结尾:"…以提升未来类似场景下的判断质量。是否需要?" —— 反思里
    不该反问用户。剥到最后一个"完整句"为止;找不到就原样返回(不硬截)。
    """
    t = (text or "").rstrip()
    for _ in range(3):
        if not t:
            return t
        # 取最后一段(空行分隔)
        parts = re.split(r"\n\s*\n", t)
        last = parts[-1].strip()
        keep = "\n\n".join(parts[:-1]).rstrip()
        if not last:
            return keep
        # 末段含兜售标记,或末段就是个问句 → 丢掉末段
        if any(m in last for m in _TAIL_OFFER_MARKERS) or last.endswith(("？", "?")):
            if keep:
                t = keep
                continue
        # 末段没标记但以问号结尾:只削掉最后那个问句
        if last.endswith(("？", "?")):
            idx = max(last.rfind("。"), last.rfind("！"), last.rfind("!"))
            if idx > 0:
                t = (keep + "\n\n" + last[:idx + 1]).strip() if keep else last[:idx + 1].strip()
                continue
        return t
    return t


def _strip_emoji(text: str) -> str:
    """去掉 emoji(正式文档里不体面;2026-09-19 体检发现 8 条反思里带 📌🔍👉🚩)。"""
    return _EMOJI.sub("", text or "")


def _strip_tail_rules(text: str) -> str:
    """去掉结尾孤零零的 markdown 分隔线(剥掉结尾段落后常留下一条 `---`)。"""
    t = (text or "").rstrip()
    for _ in range(3):
        t2 = re.sub(r"(?:\n\s*)*[-*_]{3,}[ \t]*$", "", t).rstrip()
        if t2 == t:
            break
        t = t2
    return t


def _looks_like_meta(seg: str) -> bool:
    return any(m in seg for m in _META_MARKERS)


def _strip_boilerplate(text: str) -> str:
    """剥掉开头的客套/元话语(只剥开头,不碰正文),最多剥 3 段。

    实测三种开头都要能剥掉:
    1. "当然可以。以下是我对这次完整咨询过程的系统性反思与深度剖析："
    2. "我将以中文回应，并严格遵循你提出的八个维度，不预设『正确』或『错误』。"
    3. "当然可以。以下是我对……的反思：\\n\\n### 1. …"
    """
    t = (text or "").lstrip()
    for _ in range(3):
        head = t[:80]
        if not head:
            break
        # 先试"客套句"完整剥法(到句读为止)
        if any(head.startswith(b) for b in _BOILERPLATE_OPENERS):
            cut = -1
            for sep in _BOILERPLATE_END:
                i = t.find(sep)
                if i != -1 and (cut == -1 or i < cut):
                    cut = i + len(sep)
            if 0 < cut <= 160:
                t = t[cut:].lstrip()
                continue
        # 再试"元话语首句"(到第一个句末标点/换行为止,且整句要短)
        m = _META_END.search(t[:200])
        seg = t[:m.end()] if m else t[:120]
        if _looks_like_meta(seg) and len(seg) <= 150:
            t = t[len(seg):].lstrip()
            continue
        break
    # 剥掉正文前的 markdown 分隔线(剥完客套常剩一条 "---";也可能和正文同一行)
    t = re.sub(r"^(?:[-*_]{3,}[ \t]*(?:\n+|$))+", "", t.lstrip())
    t = _strip_emoji(t)
    return _strip_tail_rules(_strip_tail_offer(t)).lstrip()



def run_reflection(llm, rec: dict, max_tokens: int = 4096) -> dict:
    """用本地 qwen3(同一 Investment AI 模型)生成 8 维 Reflection。

    llm: 本地 Ollama client(0904:与判断同源;不使用外部模型)
    rec: 一次 Run 的记录(供 assemble_reflection_material)
    返回 {"material": 输入, "text": Reflection 输出, "stripped_opener": bool}
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
    raw = text or ""
    cleaned = _strip_boilerplate(raw)
    return {"material": material, "text": cleaned,
            "stripped_opener": bool(raw) and cleaned != raw.lstrip()}


# Router 风险锚点(06 第 7 节规则 4 的落地提示)
ROUTER_RISK_ANCHOR = (
    "\n风险等级判定锚点(辅助,不是替代专业判断):\n"
    "- 若问题涉及用户重大资金损失、不可逆个人后果或系统性影响 → High;\n"
    "- 若仅涉及信息表述、措辞、格式或轻度流程问题 → Low;\n"
    "- 其余 → Medium。"
)

ROUTER_JSON_HINT = (
    "\n\n输出要求:把识别出的每个问题输出为 JSON 数组,不要输出其他内容:\n"
    '[{"summary": "行为/判断的陈述句", "risk_note": "风险或错在哪", '
    '"field": "专业领域", "risk": "High|Medium|Low", '
    '"routing_reason": "路由理由"}, ...]\n'
    "若没有需要专业审核的问题,输出 []\n"
    "summary 必须是陈述句(描述做过/没做过的具体行为或判断),**不要写成疑问句**。\n"
    "格式硬性要求:不要使用 ```json 代码围栏;summary/field/routing_reason 等"
    "字段内容中一律不要出现英文双引号(\"),需要引用原文时用中文引号『』或“”。"
)

# 疑问句判定(2026-09-19):模型偶尔仍把"待查的疑问"当问题输出。
# 这里做一层确定性检查:命中的会被要求改写一遍;再不合格就**如实标注** style="question",
# 让面板显出来(不许静默把疑问句当问题)。
_QUESTION_TAIL = ("?", "？")
_QUESTION_STARTS = ("是否", "能否", "会不会", "是不是", "有没有", "为什么", "是什么",
                    "如何", "怎么", "怎样", "何时", "多少", "哪些", "哪一种", "可否")


def looks_like_question(text: str) -> bool:
    """粗略判断一句话是不是疑问句(问号结尾或疑问词开头)。"""
    t = (text or "").strip()
    if not t:
        return False
    if t.endswith(_QUESTION_TAIL):
        return True
    return any(t.startswith(w) for w in _QUESTION_STARTS)


ROUTER_REWRITE_HINT = (
    "上面这些条目的 summary 写成了疑问句,而我们要的是**问题**——即具体的行为/判断"
    "以及它带来的风险。请把下面这些条目改写成陈述句(谁在什么依据下做了什么/没做什么),"
    "并补上 risk_note(风险或错在哪);其余字段保持原样。"
    "只输出 JSON 数组,不要输出其他内容:\n"
    '[{"id": "原 id", "summary": "改写后的陈述句", "risk_note": "风险或错在哪"}, ...]\n'
)

_ROUTER_RISKS = {"high", "medium", "low"}


def _parse_router_json(text: str) -> list:
    """从 Router 输出提取 issues 列表。

    容忍:前后杂文本、```json 代码围栏、首尾空白;数组内个别畸形项跳过。
    """
    import json as _json
    import re as _re

    if not text:
        return []
    t = str(text)
    # 剥 markdown 代码围栏(```json ... ```)
    m = _re.search(r"```(?:json)?\s*(.*?)```", t, _re.S)
    if m:
        t = m.group(1)
    # 取第一个 [ 到最后一个 ] 之间(容忍尾部解释文字)
    i0 = t.find("[")
    i1 = t.rfind("]")
    if i0 < 0 or i1 <= i0:
        return []
    try:
        arr = _json.loads(t[i0:i1 + 1])
    except _json.JSONDecodeError:
        # 整体解析失败:尝试逐行剥离畸形(常见:某字段含未转义引号)
        arr = _line_tolerant_parse(t[i0:i1 + 1])
    issues = []
    if not isinstance(arr, list):
        return []
    for i, it in enumerate(arr):
        if not isinstance(it, dict):
            continue
        risk = str(it.get("risk", it.get("risk_level", ""))).strip().lower()
        if risk not in _ROUTER_RISKS:
            risk = "medium"
        summary = str(it.get("summary", "")).strip()
        # 字段名容忍:实测本地模型爱用 required_expert/risk_level 而不是 field/risk,
        # 只认 field 会让"专业领域"整列空着(2026-09-19 体检:B-1613 五条全空、
        # B-1710 有四条 risk_note 却 field 全空 —— 不是模型没答,是解析没认)。
        field = str(it.get("field", "")
                    or it.get("required_expert", "")
                    or it.get("expert", "")
                    or it.get("professional_field", "")).strip()
        reason = str(it.get("routing_reason", "") or it.get("reason", "")).strip()
        risk_note = str(it.get("risk_note", "") or it.get("note", "")).strip()
        if not summary and not field:
            continue
        issues.append({
            "id": "issue-{}".format(i + 1),
            "summary": summary,
            "risk_note": risk_note,
            "field": field,
            "risk": risk,
            "routing_reason": reason,
            # style: "behavior"=陈述句行为/判断(要的就是这个);"question"=仍是疑问句(未达标,
            # 面板会标出来,不静默)。见 looks_like_question()。
            "style": "question" if looks_like_question(summary) else "behavior",
        })
    return issues


def _line_tolerant_parse(chunk: str) -> list:
    """整体 JSON 解析失败时的兜底:按顶层对象块切分逐个重试。

    括号深度切分不依赖引号配对,因此个别字段含未转义引号时仍能保住
    其他合法对象。仍失败返回空列表。
    """
    import json as _json

    chunk = (chunk or "").strip()
    # 剥外层数组括号(顶层切分按对象进行)
    if chunk.startswith("[") and chunk.endswith("]"):
        chunk = chunk[1:-1]
    parts = []
    depth = 0
    buf = []
    for ch in chunk:
        if ch == "{":
            if depth == 0:
                buf = [ch]
            else:
                buf.append(ch)
            depth += 1
            continue
        if ch == "}":
            depth -= 1
            buf.append(ch)
            if depth == 0:
                parts.append("".join(buf))
                buf = []
            continue
        if depth > 0:
            buf.append(ch)
    out = []
    for p in parts:
        p = p.strip()
        if not p.startswith("{"):
            continue
        try:
            obj = _json.loads(p)
        except _json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _rewrite_question_issues(llm, issues: list, max_tokens: int = 1024) -> list:
    """把"写成了疑问句"的条目交给模型改写一次(陈述句 + risk_note)。

    改写不成也不丢:原样保留并把 style 标成 "question",面板会显出来(不许静默)。
    """
    import json as _json

    bad = [x for x in issues if x.get("style") == "question"]
    if not bad:
        return issues
    payload = _json.dumps([{"id": x["id"], "summary": x["summary"]} for x in bad],
                          ensure_ascii=False)
    try:
        text = llm.chat([
            {"role": "system", "content": "You are the Reflection Router. Respond in Chinese."},
            {"role": "user", "content": ROUTER_REWRITE_HINT + "\n" + payload},
        ], temperature=0.2, max_tokens=max_tokens)
        fixed = {str(it.get("id", "")): it for it in _parse_rewrite_json(text or "")}
    except Exception:  # noqa: BLE001 - 改写失败不该让整条记录没了;原样保留 + 标注
        return issues
    for x in issues:
        it = fixed.get(x["id"])
        if not it:
            continue
        new_summary = str(it.get("summary", "")).strip()
        if new_summary and not looks_like_question(new_summary):
            x["summary"] = new_summary
            x["style"] = "behavior"
        note = str(it.get("risk_note", "")).strip()
        if note and not x.get("risk_note"):
            x["risk_note"] = note
    return issues


def _parse_rewrite_json(text: str) -> list:
    """改写结果解析:容忍围栏/杂文本,复用同一套容错。"""
    return _parse_router_json(text)


def run_router(llm, reflection_text: str, material: str = "",
               max_tokens: int = 2048) -> dict:
    """Router:把 Reflection 中已出现的问题拆分并路由,输出结构化 issues。

    llm: 独立模型(本地 qwen3 或外部均可;M3 先用本地,后续可切)
    返回 {raw, issues:[{id,summary,risk_note,field,risk,routing_reason,style}]}

    2026-09-19:用户要求"问题"必须是**带风险的行为/判断**,不能是疑问句。
    所以这里多一步:检出疑问句 → 让模型改写一遍 → 仍有疑问句就标 style=question。
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
    return {"raw": raw, "issues": _rewrite_question_issues(llm, issues)}
