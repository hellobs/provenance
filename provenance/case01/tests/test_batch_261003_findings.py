# -*- coding: utf-8 -*-
"""批次 261003-165042 深度分析挖出的五个缺口的契约守卫(2026-10-03)。

那批 8b 批量样本(44 条,成功 43)表面健康:branch 有分布、反思有质量分、
err.log 干净的。但逐条扒开产物后发现四类**被伪装成好数据**的坏数据 ——
全部同一类病:LLM 出了问题,而代码把失败塌成了合法的业务结果。
项目铁律是"不允许静默",所以每一条都钉一个可复现的守卫:

1. **C 线条件监控永远不触发(问题 1)**。11/11 条 C 线的 `c_plan` 六个字段全是默认值
   (`action=wait / buy_fraction=0.0 / condition="" / trigger.type=none`),于是
   `condition_monitor` 每天 `fired=false` —— 条件从一开始就不存在,不是没等到。
   根因:`ConditionPlanParser` 用 `max_tokens=400` 调 Ollama 的 OpenAI 兼容端点,
   实测 content **恒为空**(0/3);提到 800 后 3/3 解析成功并给出真触发条件。
   注意该端点**不认 think 参数**,所以只能加预算,关思考没用。

2. **plan 解析失败被伪装成"AI 建议继续等待"**。空输出 → `_parse_json` 返回 `{}`
   → `_sanitize({})` 补成一份看起来完全正常的"等待计划",还标着 `judge="llm-plan"`。
   出事后没人知道发生过什么。现在失败必须标成 `llm-plan-error` 并留原文。

3. **Router 空输出被当成"反思里没有问题"**。9/41 条 `router.raw==""` 且
   `issues==[]`,不抛不记 —— 专家侧无单可建,样本白跑,统计被污染。
   空输出只可能是上游 thinking 吃光预算,必须报错。

4. **批量样本全无一致性戳**。`orchestrator.run_case01` 直接 `rec.save()` 落盘、
   绕过了 mavis pipeline 的盖章,43 条**全都没有** `consistency` 段,平台侧
   `quality_of` 只能一律判 `unverified` —— 分不出"自洽"与"压根没查过"。

5. **超时打死整条 run + 失败原因在批处理层丢失**。`urlopen` 超时抛的是
   `socket.timeout`(=`TimeoutError`),它**不是** `URLError` 子类,原先漏在重试
   白名单外 → 8b 单次生成超 120s 时不重试直接冒泡,run.json 全丢(批次 014)。
   连带:`batch_run` 的 `log_tail` 固定取末尾 1500 字,而重试会在 traceback
   **之后**继续写正常输出,于是失败原因根本没进台账。
"""
import inspect
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.world.branch import (  # noqa: E402
    PLAN_MAX_TOKENS, ConditionPlanParser,
)
from case01.consistency import attach_consistency  # noqa: E402
from case01.reflection import run_router  # noqa: E402
from case01.tools.batch_run import _failure_tail  # noqa: E402


# ---------------------------------------------------------------------------
# 1. C 线 plan 的输出预算:不能再退回 400
# ---------------------------------------------------------------------------
class _RecordingLLM:
    """记录每次 chat 收到的 max_tokens,并按预设序列返回内容。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.seen_max_tokens = []
        self.calls = 0

    def chat(self, messages, temperature=0.7, max_tokens=1024, **kw):
        self.seen_max_tokens.append(max_tokens)
        self.calls += 1
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def test_plan_budget_is_big_enough_for_8b():
    """400 实测 content 恒空;这里钉住下限,防有人改回去。"""
    assert PLAN_MAX_TOKENS >= 800, \
        "C 线 plan 的 max_tokens 不得低于 800(2026-10-03 实测 400 时 0/3 解析成功)"


def test_plan_call_actually_uses_the_budget_constant():
    """常量改了但调用点没跟上,等于没改(防"改了常量没改用"的空修)。"""
    llm = _RecordingLLM(['{"action":"wait","condition":"等公告"}'])
    ConditionPlanParser(llm).parse("答案")
    assert llm.seen_max_tokens and all(
        m >= 800 for m in llm.seen_max_tokens), llm.seen_max_tokens


# ---------------------------------------------------------------------------
# 2. plan 解析失败不许伪装成业务决策
# ---------------------------------------------------------------------------
def test_plan_empty_output_is_marked_as_error_not_as_wait():
    """空 content 两次 → 必须 llm-plan-error,而不是一份合法的"等待计划"。"""
    llm = _RecordingLLM(["", ""])
    p = ConditionPlanParser(llm).parse("答案")
    assert p["judge"] == "llm-plan-error", \
        "解析失败必须与真决策可区分,否则失败被伪装成'AI 建议继续等待'"
    assert p.get("error"), "失败原因要落盘(不允许静默:出事后知道是谁的锅)"
    assert p.get("raw_outputs") == ["", ""], "原始输出要留着复盘"


def test_plan_failure_still_fails_safe():
    """失败也必须是**保守**默认:不买、不触发 —— 坏数据不许变成真金白银的买入。"""
    p = ConditionPlanParser(_RecordingLLM(["", ""])).parse("答案")
    assert p["action"] == "wait"
    assert p["buy_fraction"] == 0.0
    assert p["trigger"]["type"] == "none"


def test_plan_retries_once_before_giving_up():
    """第一次空、第二次正常 → 应成功,而不是第一次就判死。"""
    llm = _RecordingLLM(["", '{"action":"wait","buy_fraction":0.2,'
                              '"condition":"等签约","trigger":null}'])
    p = ConditionPlanParser(llm).parse("答案")
    assert p["judge"] == "llm-plan", p
    assert p["attempts"] == 2
    assert p["buy_fraction"] == 0.2
    assert p["trigger"]["type"] == "keyword"


def test_plan_success_path_keeps_normal_judge_tag():
    """成功路径的 judge 仍叫 llm-plan(别把正常样本也标成错误)。"""
    p = ConditionPlanParser(_RecordingLLM(
        ['{"action":"wait","buy_fraction":0.3,"condition":"等公告","trigger":null}']
    )).parse("答案")
    assert p["judge"] == "llm-plan"
    assert "error" not in p


def test_plan_parse_json_keeps_legacy_empty_dict_contract():
    """`_parse_json` 的旧契约(失败返回 {})不许破,既有测试与调用方依赖它。"""
    assert ConditionPlanParser._parse_json("no json here") == {}


def test_plan_parse_json_err_distinguishes_failure_modes():
    """失败要给出**具体**原因,而不是一律塌成 {}。

    两种形态要分得开:正则要闭合的 `{...}`,所以"JSON 被截断没闭合"落在
    "没有 JSON 对象"这一支;"括号齐全但语法非法"才落到 decode 那一支。
    """
    _, e_empty = ConditionPlanParser._parse_json_err("")
    _, e_nojson = ConditionPlanParser._parse_json_err("完全不是 JSON")
    _, e_unclosed = ConditionPlanParser._parse_json_err('{"action": "wait"')
    _, e_bad = ConditionPlanParser._parse_json_err('{"action": "wait",}')
    assert e_empty and e_nojson and e_unclosed and e_bad
    assert "JSON 不完整" in e_bad or "非法" in e_bad, e_bad
    assert "没有 JSON 对象" in e_unclosed, e_unclosed


# ---------------------------------------------------------------------------
# 3. Router 空输出必须报错,不能当成"没有问题"
# ---------------------------------------------------------------------------
class _BlankLLM:
    def chat(self, messages, temperature=0.7, max_tokens=1024, **kw):
        return ""


def test_router_blank_output_raises_instead_of_reporting_zero_issues():
    """Router 职责是一定拆出问题;空 content 只可能是上游挂了。"""
    with pytest.raises(RuntimeError) as ei:
        run_router(_BlankLLM(), "反思正文里有一些可拆的问题。")
    assert "空" in str(ei.value), str(ei.value)


def test_router_error_mentions_it_is_not_zero_issues():
    """错误文案要点明"空输出 ≠ 没有问题",免得下游误当成干净样本。"""
    with pytest.raises(RuntimeError) as ei:
        run_router(_BlankLLM(), "正文")
    assert "问题" in str(ei.value), str(ei.value)


# ---------------------------------------------------------------------------
# 4. 一致性戳:两条落盘路径共用同一份实现
# ---------------------------------------------------------------------------
def _record(branch="B"):
    return {
        "branch": branch,
        "branch_action": {"branch": branch, "judge": "llm"},
        "turns": [
            {"speaker": "ethan", "date": "2026-08-27", "text": "值得买吗?"},
            {"speaker": "investment_ai", "date": "2026-08-27",
             "text": "I cannot confirm the rumour is true. Not sufficient to "
                     "recommend buying at this price."},
        ],
    }


def test_attach_consistency_writes_stamp_and_source():
    """盖章要同时写 consistency 与 branch_action.source,两者都缺平台就判不了。"""
    out = attach_consistency(_record(), branch_source="judge")
    assert out["consistency"]["verdict"] in (
        "consistent", "inconsistent", "unknown"), out["consistency"]
    assert out["consistency"]["method"] == "quick_scan"
    assert out["branch_action"]["source"] == "judge"
    assert out["consistency"]["branch_source"] == "judge"


def test_attach_consistency_does_not_mutate_input():
    """返回新 dict:别把调用方正在用的记录就地改了。"""
    rec = _record()
    attach_consistency(rec, branch_source="judge")
    assert "consistency" not in rec, "盖章不该就地改调用方的 dict"


def test_attach_consistency_keeps_existing_source():
    """已写好的 source 不能被默认值盖掉(judge 记录被标成 preset 是误标注)。"""
    rec = _record()
    rec["branch_action"]["source"] = "judge-failed"
    out = attach_consistency(rec, branch_source="preset")
    assert out["branch_action"]["source"] == "judge-failed"


def test_pipeline_and_case01_share_one_implementation():
    """防两份实现漂移:pipeline 必须转调 consistency.attach_consistency。"""
    from mavis_case01_injector import pipeline
    src = inspect.getsource(pipeline._attach_consistency)
    assert "attach_consistency" in src, \
        "pipeline 必须转调公开实现,别再抄一份(两处一漂移就又是一批假数据)"


def test_orchestrator_stamps_before_save():
    """结构守卫:一致性戳必须在 `rec.save()` **之前**盖。

    为什么用源码断言:真正的故障就是"顺序错了/压根没盖",而这条路径要跑真实
    LLM 才能走完,单测里跑不起来。钉住调用顺序比钉住行为更防回归。
    """
    from case01 import orchestrator
    src = inspect.getsource(orchestrator.run_case01)
    assert "attach_consistency" in src, "编排器落盘路径必须盖一致性戳"
    # 匹配**真正的调用**而不是注释里出现的字样(注释里也提到了 rec.save)。
    assert src.index("attach_consistency") < src.index("p = rec.save()"), \
        "盖章必须在 save 之前:落盘后再盖等于没盖"


# ---------------------------------------------------------------------------
# 5. 超时要重试;失败原因要进台账
# ---------------------------------------------------------------------------
def test_llm_retries_on_timeout_error():
    """socket.timeout(=TimeoutError)不是 URLError 子类,漏了就不重试。"""
    from mavis_case01_injector import llm as llm_mod

    calls = {"n": 0}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"finish_reason": "stop",
                                            "message": {"content": "ok"}}]}
                              ).encode("utf-8")

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timed out")
        return _Resp()

    orig = llm_mod.urllib.request.urlopen
    llm_mod.urllib.request.urlopen = fake_urlopen
    try:
        c = llm_mod.OllamaClient(chat_model="qwen3:8b", retries=3)
        c.timeout = 5
        assert c.chat([{"role": "user", "content": "x"}]) == "ok"
    finally:
        llm_mod.urllib.request.urlopen = orig
    assert calls["n"] == 2, "第一次超时应重试,而不是直接冒泡打死整条 run"


def test_llm_gives_up_with_runtime_error_not_bare_timeout():
    """重试用尽后仍要抛 RuntimeError(带上下文),别把裸 TimeoutError 漏出去。"""
    from mavis_case01_injector import llm as llm_mod

    def fake_urlopen(req, timeout=None):
        raise TimeoutError("timed out")

    orig = llm_mod.urllib.request.urlopen
    llm_mod.urllib.request.urlopen = fake_urlopen
    try:
        c = llm_mod.OllamaClient(chat_model="qwen3:8b", retries=2)
        c.timeout = 5
        with pytest.raises(RuntimeError) as ei:
            c.chat([{"role": "user", "content": "x"}])
        assert "TimeoutError" in str(ei.value) or "timed out" in str(ei.value)
    finally:
        llm_mod.urllib.request.urlopen = orig


def test_failure_tail_captures_traceback_not_just_file_end():
    """重试会在 traceback 之后继续写正常输出 —— 固定取末尾就抓不到失败原因。"""
    body = (
        "启动\n"
        "Traceback (most recent call last):\n"
        "  File \"x.py\", line 1, in <module>\n"
        "TimeoutError: timed out\n"
        "===== retry =====\n"
        "重试后跑完了,这里是一堆正常输出,占了很长很长很长很长很长很长很长"
        "很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False,
                                     encoding="utf-8") as f:
        f.write(body)
        path = f.name
    try:
        tail = _failure_tail(path, limit=200)
        assert "TimeoutError" in tail, "台账里必须能看到真正的失败原因"
        assert "Traceback" in tail
    finally:
        os.unlink(path)


def test_failure_tail_falls_back_when_no_traceback():
    """没有 traceback 的失败(比如非空但异常退出)也别返回空。"""
    with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False,
                                     encoding="utf-8") as f:
        f.write("普通输出\n" * 200)
        path = f.name
    try:
        assert _failure_tail(path, limit=100).strip()
    finally:
        os.unlink(path)


def test_failure_tail_missing_file_is_empty_not_crash():
    """日志文件不存在时返回空串 —— 记账不该因为取日志失败而崩掉。"""
    assert _failure_tail(r"D:\zzr\__definitely_not_here__.log") == ""


# ---------------------------------------------------------------------------
# 6. 到点必须真的终止子进程(2026-10-05 体检 §七)
# ---------------------------------------------------------------------------
def test_timeout_actually_terminates_the_subprocess():
    """超时后 `_run_with_probe` 必须终止子进程,而不是无限 `proc.wait()`。

    此前实现:轮询到 deadline 后直接 `proc.wait()` —— 子进程还在跑就永远等下去,
    deadline 形同虚设(2026-10-03 h120seed 那次 3 条 lane 卡 8h25m)。
    这里用一个**睡 60s** 的子进程配 2s 超时:若没真终止,本测试会挂住;
    真终止则秒回且 rc 非零。
    """
    import time as _time
    from case01.tools import batch_run as br

    log_path = tempfile.mktemp(suffix=".log")
    cmd = [sys.executable, "-c", "import time; time.sleep(60)"]
    t0 = _time.time()
    rc, seen = br._run_with_probe(cmd, os.getcwd(), dict(os.environ),
                                  log_path, timeout=2)
    elapsed = _time.time() - t0
    try:
        assert elapsed < 30, "子进程没被终止,耗了 {:.0f}s".format(elapsed)
        assert rc != 0, "超时应记成失败(rc!=0),实际 rc={}".format(rc)
        with open(log_path, encoding="utf-8") as f:
            assert "TIMEOUT" in f.read(), "日志里应留下超时痕迹"
    finally:
        if os.path.exists(log_path):
            os.unlink(log_path)


def test_normal_completion_returns_its_own_rc():
    """没超时的正常子进程:rc 原样返回(终止逻辑不该误伤正常路径)。"""
    from case01.tools import batch_run as br

    log_path = tempfile.mktemp(suffix=".log")
    cmd = [sys.executable, "-c", "raise SystemExit(0)"]
    rc, _ = br._run_with_probe(cmd, os.getcwd(), dict(os.environ),
                               log_path, timeout=30)
    try:
        assert rc == 0
    finally:
        if os.path.exists(log_path):
            os.unlink(log_path)
