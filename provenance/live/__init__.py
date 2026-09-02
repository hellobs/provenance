"""provenance Web 服务包:模块化拆分自 live_fastapi.py。

- state.py       : 全局状态(BASE_DIR/sim_state/manager/server/compressor)与缓存工具
- chart.py       : 倾向曲线 PNG 渲染(matplotlib)
- reflections.py : 反思标记存储(LoRA 线数据源)
- routes.py      : FastAPI 路由(页面/嵌入/API/WebSocket)
- live_fastapi.py : CLI 入口(argparse + 模拟线程 + uvicorn)
"""
