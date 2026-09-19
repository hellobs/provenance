# -*- coding: utf-8 -*-
"""live_switch 的纯函数测试。

只测**不碰进程**的那部分:

- `n_instances`:按 ppid 折算"逻辑实例数"。uv 建的 venv 里 `python.exe` 是 trampoline,
  会再拉起真正的解释器,所以一个逻辑实例对应父子两个 PID——不折算就会把 1 报成 2。
- `_state`:状态文案。只报能从外部确证的东西(case00 用倾向角色数可靠;
  case01 用 /health 的 finished/finish_reason 分清"在推演/已跑完/仅审阅")。
- 端口映射。

真正起停进程的部分**不做自动化测试**:它要杀进程、依赖 netstat/PowerShell,
不适合放进 CI(那是 `--status` / `--start` 手工验收的活)。
"""
from live_switch import LIVE, _state, n_instances


def test_n_instances_folds_trampoline_pair():
    # 1 个实例:trampoline(100) 拉起真解释器(200)
    assert n_instances({100: 999, 200: 100}) == 1
    # 只有子进程被匹配到(父不在集合里),也算 1 个实例
    assert n_instances({200: 100}) == 1
    # 2 个实例:两组父子对(这正是"游离实例"的样子)
    assert n_instances({100: 999, 200: 100, 300: 999, 400: 300}) == 2
    # 互不相干的独立进程
    assert n_instances({10: 999, 20: 999}) == 2
    assert n_instances({}) == 0


def test_state_only_claims_what_it_can_prove():
    assert _state({"case": "case01", "listening": False, "simulating": None}) == "未启动"
    # case01:靠 /health 的 finished 分清"还在推演"与"跑完在保持"(2026-09-19 起)
    assert _state({"case": "case01", "listening": True, "simulating": True}) == "在推演"
    assert _state({"case": "case01", "listening": True, "simulating": False,
                   "finish_reason": "run_finished"}) == "在听(已跑完)"
    # 仅审阅(--review-only):服务在,但没有在推演
    assert _state({"case": "case01", "listening": True, "simulating": False,
                   "finish_reason": "review_only"}) == "仅审阅(未在推演)"
    # case00:倾向角色数是可靠信号
    assert _state({"case": "case00", "listening": True, "simulating": True}) == "在推演"
    assert _state({"case": "case00", "listening": True,
                   "simulating": False}) == "在听(未在推演:--no-sim)"


def test_two_live_faces_use_distinct_ports():
    assert LIVE["case00"] != LIVE["case01"]
    assert set(LIVE) == {"case00", "case01"}
