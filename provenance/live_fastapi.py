"""实时模拟 + 可视化服务(FastAPI + WebSocket)—— CLI 入口。

路由/页面/API 在 live/routes.py;模拟装配在 live/simulation.py;
全局状态在 live/state.py。本文件只做:参数解析 → 启动模拟线程 → uvicorn。

用法不变:
  python live_fastapi.py --name sim --start "20250213-09:30" --stride 2 --step 0 --port 5010
  python live_fastapi.py --resume --name stock-en7 --step 0 --port 5010
"""
import argparse
import json
import os
import threading

import uvicorn

from live import state
from live.routes import app  # noqa: F401  (uvicorn live_fastapi:app 亦可)

BASE_DIR = state.BASE_DIR


def _start_simulation(name, sim_config, start_step, step, stride):
    from live.simulation import run_simulation
    thread = threading.Thread(
        target=run_simulation,
        args=(name, sim_config, start_step, step, stride),
        daemon=True,
    )
    thread.start()


def main():
    parser = argparse.ArgumentParser(description="live simulation server (FastAPI)")
    parser.add_argument("--name", type=str, default="", help="The simulation name")
    parser.add_argument("--start", type=str, default="20250213-09:30", help="The starting time of the simulated ville")
    parser.add_argument("--resume", action="store_true", help="Resume running the simulation")
    parser.add_argument("--step", type=int, default=0, help="The simulate step (<=0 means run forever)")
    parser.add_argument("--stride", type=int, default=2, help="The step stride in minute")
    parser.add_argument("--port", type=int, default=5010, help="The server port")
    # 绑定地址:默认只绑本机(安全);平台侧跨机对接时给 0.0.0.0 或用 LIVE_HOST。
    parser.add_argument("--host", type=str, default=os.environ.get("LIVE_HOST", "127.0.0.1"),
                        help="Bind address (use 0.0.0.0 to let the platform reach it)")
    parser.add_argument("--no-sim", action="store_true",
                        help="Only serve the Web layer (pages/API/embed), do not start the simulation thread")
    args = parser.parse_args()
    # 绑非本机地址必须显式声明:5010 有写端点且全栈无鉴权(安全体检 2026-09-23)。
    from live.netguard import require_explicit_remote
    require_explicit_remote(args.host, where="5010 实时面")

    name = args.name
    if len(name) < 1:
        name = input("Please enter a simulation name (e.g. sim-test): ")

    if args.resume:
        while not os.path.exists(state.checkpoint_file(name)):
            name = input(f"'{name}' doesn't exists, please re-enter the simulation name: ")
    else:
        if os.path.exists(state.checkpoint_file(name)):
            # 存档名冲突:自动追加时间戳后缀(后台/服务化场景无 stdin,不能阻塞等输入)
            import time as _time
            suffix = _time.strftime("%m%d-%H%M%S")
            name = f"{name}-{suffix}"
            print(f"Simulation name '{args.name}' already exists, using '{name}'")

    checkpoints_folder = state.checkpoint_file(name)
    if args.resume:
        from mavisframework.config.loader import load_config_from_log
        sim_config = load_config_from_log(checkpoints_folder)
        if sim_config is None:
            print("No checkpoint file found to resume running.")
            exit(0)
        start_step = sim_config["step"]
        print("resume from step {} @ {}".format(start_step, sim_config["time"]))
    else:
        from live.simulation import _discover_agent_names
        from mavisframework.config.loader import load_config
        sim_config = load_config(args.start, args.stride, _discover_agent_names())
        start_step = 0

    state.sim_state["start_time"] = sim_config["time"]["start"]
    state.sim_state["stride"] = args.stride

    # 时间轴横轴起点:resume 时 config 的 start 是"恢复时刻",并非模拟真实起点。
    # 从 checkpoint 目录最早 simulate-*.json 的文件名反推真实起点(如 09:30),
    # 供干预时间轴展示完整"从模拟开始到当前"的进程。
    if args.resume and os.path.isdir(checkpoints_folder):
        import glob as _g
        import datetime as _dt
        _ck_files = sorted(_g.glob(os.path.join(checkpoints_folder, "simulate-*.json")))
        if _ck_files:
            _first = os.path.basename(_ck_files[0]).replace("simulate-", "").replace(".json", "")
            try:
                _dt0 = _dt.datetime.strptime(_first, "%Y%m%d-%H%M")
                state.sim_state["start_time"] = _dt0.strftime("%Y%m%d-%H:%M")
            except (ValueError, TypeError):
                pass  # 文件名异常时保留 config 的 start

    if args.no_sim:
        print("Simulation thread disabled (--no-sim): serving Web layer only.")
    else:
        _start_simulation(name, sim_config, start_step, args.step, args.stride)
        print(f"Live simulation '{name}' started (FastAPI). Open http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
