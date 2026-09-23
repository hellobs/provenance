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
from live_switch import CMD_NEEDLE, LIVE, LIVE_NAME, _state, n_instances


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


def test_two_live_faces_share_single_live_entry():
    # 5010 是**唯一实时入口**,两 case 互为互斥、共用同一端口;端口不归属任一 case,
    # 所以"哪个 case 在跑"只能靠命令行特征认(见 live_switch 模块 docstring)。
    assert set(LIVE) == {"case00", "case01"}
    # 唯一实时入口:两 case 端口必须相同;且都落在"唯一实时端口"这一份常量上。
    assert len({LIVE[c] for c in LIVE}) == 1
    # 命令行为何是判属依据:同端口之下,两 case 必须有各自唯一的命令行特征(不能互认)。
    assert set(CMD_NEEDLE) == {"case00", "case01"}
    assert CMD_NEEDLE["case00"] != CMD_NEEDLE["case01"]
    assert all(CMD_NEEDLE[c] for c in CMD_NEEDLE)


def test_reachable_reports_the_truth_for_open_and_closed_ports():
    """跨机提示里的"本机自测通/不通"必须真测:开着 → 通,没人听 → 不通。

    2026-09-23:一开始只把候选地址列出来就当"平台侧可用",而实测这台机器上
    以太网那块网卡是 Disconnected(地址还在、连不上),于是列表里第二个地址是假的。
    """
    import socket

    from live_switch import _reachable

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert "通" in _reachable("127.0.0.1", port)
    finally:
        srv.close()
    assert "不通" in _reachable("127.0.0.1", port), "端口关了还说通,就是在骗人"


def test_lan_ip_list_never_advertises_loopback_or_link_local():
    """候选地址不许把回环/169.254 当"跨机可用"报出去(那是编的)。"""
    from live_switch import _all_lan_ips

    ips = _all_lan_ips()
    assert isinstance(ips, list)
    for ip in ips:
        assert not ip.startswith("127.") and not ip.startswith("169.254."), ip
    assert len(ips) == len(set(ips)), "候选地址不该重复"
