# -*- coding: utf-8 -*-
"""case_engine/reflection.py 单元测试:清洗工具 + 材料组装 + Router JSON 容错。"""
from case_engine.reflection import (
    _strip_boilerplate, _strip_tail_offer, _strip_emoji, _strip_tail_rules,
    assemble_reflection_material, looks_like_question, _parse_router_json,
    run_reflection, run_router,
)


# ---- 清洗工具 ----
def test_strip_boilerplate_opener():
    raw = "当然可以。以下是我对这次事件的系统反思:\n### 1. 判断\n正文"
    assert "正文" in _strip_boilerplate(raw)
    assert not _strip_boilerplate(raw).startswith("当然")


def test_strip_boilerplate_meta():
    raw = "我将以中文回应，并严格遵循你提出的几个维度，不预设对错。\n正文内容"
    out = _strip_boilerplate(raw)
    assert "正文内容" in out
    assert "我将以" not in out


def test_strip_tail_offer():
    raw = "综上,这是本次反思。\n是否需要我继续帮你分析?"
    out = _strip_tail_offer(raw)
    assert "是否需要" not in out
    assert "这是本次反思" in out


def test_strip_emoji_removes_emoji():
    out = _strip_emoji("正文📌 要点🔍")
    assert "📌" not in out and "🔍" not in out
    assert "正文" in out and "要点" in out


def test_strip_tail_rules():
    assert _strip_tail_rules("正文\n---") == "正文"


# ---- 材料组装(speaker/键注入) ----
def _rec():
    return {
        "turns": [
            {"speaker": "q", "date": "2026-01-01", "text": "你好"},
            {"speaker": "assistant", "date": "2026-01-01", "text": "你好,请说"},
        ],
        "retrievals": [{"hits": [{"source": "s", "type": "t", "time": "01-01"}]}],
        "events": [{"date": "01-02", "kind": "price", "summary": "close"}],
        "final_feedback": {"client": "我最终没有操作"},
    }


def test_assemble_uses_injected_speaker():
    m = assemble_reflection_material(_rec(), asker_speaker=("q",))
    assert "提问方" in m and "你" in m
    assert "[提问方" in m


def test_assemble_includes_feedback_by_key():
    m = assemble_reflection_material(_rec(), fb_asker_key="client")
    assert "我最终没有操作" in m


# ---- 疑问句判定 ----
def test_looks_like_question():
    assert looks_like_question("这是否成立?")
    assert looks_like_question("是否应该立即行动")
    assert not looks_like_question("这是在确认依据,应据此判断")


# ---- Router JSON 容错解析 ----
def test_parse_router_json_plain():
    txt = '[{"summary":"未验证依据就下判断","risk_note":"可能导致损失",' \
          '"field":"合规","risk":"High","routing_reason":"需专家"},' \
          '{"summary":"另一条","risk_note":"n","field":"f","risk":"low","routing_reason":"r"}]'
    issues = _parse_router_json(txt)
    assert len(issues) == 2
    assert issues[0]["risk"] == "high"
    assert issues[1]["risk"] == "low"
    assert issues[0]["style"] == "behavior"


def test_parse_router_json_tolerates_fence_and_surround():
    txt = '解释:```json\n[{"summary":"行为判断句","field":"f","risk":"Medium"}]\n``` 完'
    issues = _parse_router_json(txt)
    assert len(issues) == 1 and issues[0]["field"] == "f"


def test_parse_router_json_fallback_fields():
    # 模型爱用 required_expert/risk_level 而非 field/risk
    txt = '{"summary":"s"} [{"summary":"行为句","required_expert":"金融",' \
          '"risk_level":"HIGH","risk_note":"n"}]'
    issues = _parse_router_json(txt)
    assert len(issues) == 1
    assert issues[0]["field"] == "金融"
    assert issues[0]["risk"] == "high"


def test_parse_router_json_line_tolerant():
    # 个别字段含未转义引号 → 整体解析失败,转逐行容错
    txt = '[{"summary":"含没有转义"的引号","field":"a"},{"summary":"好句","field":"b"}]'
    issues = _parse_router_json(txt)
    # 至少保住合法对象(第二条)
    assert any(i["field"] == "b" for i in issues)


def test_parse_router_json_body():
    txt = '[{"summary":"这是一个疑问句吗?","field":"x"}]'
    issues = _parse_router_json(txt)
    assert issues[0]["style"] == "question"


# ---- 顶层入口(不触网,注入 stub llm) ----
class _Llm:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, msgs, temperature=None, max_tokens=None):
        self.last = msgs
        return self._reply

    def native_chat(self, msgs, temperature=None, max_tokens=None, num_ctx=None):
        self.last = msgs
        return self._reply


def test_run_reflection_cleans():
    llm = _Llm("当然可以。以下是反思:\n正文")
    out = run_reflection(llm, _rec(), asker_speaker=("q",))
    assert out["text"] == "正文"
    assert out["stripped_opener"] is True
    assert "反思:\n正文" not in out["text"]  # 已剥掉客套


def test_run_reflection_uses_injected_prompt():
    llm = _Llm("回应")
    run_reflection(llm, _rec(), reflection_system="CUSTOM_SYS")
    assert llm.last[0]["content"] == "CUSTOM_SYS"


def test_run_router_uses_injected_prompt_and_parse():
    llm = _Llm('[{"summary":"行为判断句","field":"f","risk":"High"}]')
    out = run_router(llm, "反思正文", router_prompt="CUSTOM_ROUTER")
    assert "CUSTOM_ROUTER" in llm.last[1]["content"]
    assert out["issues"][0]["field"] == "f"


def test_run_router_question_rewritten():
    # 首个疑问句 → 交给 llm 改写 stub 返回改写稿
    class _Rw:
        replies = ["[{\"id\":\"issue-1\",\"summary\":\"改为行为句\",\"risk_note\":\"n\"}]",
                   ""]
        def __init__(self):
            self.i = 0
        def chat(self, msgs, temperature=None, max_tokens=None):
            r = self.replies[min(self.i, len(self.replies) - 1)]
            self.i += 1
            return r
    llm = _Rw()
    out = run_router(llm, "正文")
    # 改写后应为陈述句
    assert all(i["style"] == "behavior" for i in out["issues"])
    assert out["issues"][0]["summary"] == "改为行为句"