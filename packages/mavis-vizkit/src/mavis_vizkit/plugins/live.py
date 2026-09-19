# -*- coding: utf-8 -*-
"""实时可视化插件:把运行中的事件**边跑边推**给前端(不是回放)。

复用点
------
- 页面:由调用方传入 `static_root` / `template_dir`(前端资源根);
  角色的贴图别名经 alias 挂载到既有目录(只影响贴图);
- 快照:新连入的客户端先收 `init` + 当前 `snapshot`,随后接收实时事件。

实现要点
--------
- uvicorn 跑在守护线程里,插件本身在引擎线程被调用 → 事件通过
  `asyncio.run_coroutine_threadsafe` 投递到 uvicorn 的事件循环;
- 服务器未启动时事件进 pending 缓冲(便于单测与无头运行,不丢语义)。
"""
import asyncio
import os
import threading
from typing import Dict, List, Optional

from .. import Visualizer, register
from .town import scenario_coords


class LiveVisualizer(Visualizer):
    """实时推送插件(启动本地服务,hook 运行中的事件)。"""

    name = "live"

    def __init__(self, host: str = "127.0.0.1", port: int = 5010,
                 alias: Optional[dict] = None,
                 roles: Optional[List[str]] = None,
                 scenario_dir: Optional[str] = None,
                 static_root: Optional[str] = None,
                 template_dir: Optional[str] = None,
                 ping_interval: float = 5.0, stride: int = 0,
                 start_datetime: str = "",
                 nodes_key: str = "nodes", meta_key: Optional[str] = None):
        if not alias:
            raise ValueError(
                "live 插件需要 alias(角色→贴图名映射),由调用方提供,不应猜默认值")
        if not scenario_dir:
            raise ValueError(
                "live 插件需要 scenario_dir(角色坐标场景目录),由调用方提供")
        if not static_root or not template_dir:
            raise ValueError(
                "live 插件需要 static_root 与 template_dir(前端资源根),由调用方提供")
        self.host = host
        self.port = int(port)
        self.alias = dict(alias)
        self.roles = list(roles or self.alias.keys())
        self.scenario_dir = scenario_dir
        self.static_root = static_root
        self.template_dir = template_dir
        self.ping_interval = float(ping_interval)
        self.stride = int(stride)
        self.start_datetime = start_datetime or "2026-08-27T09:30:00"

        self.init_pos = scenario_coords(scenario_dir, self.roles)
        self._clients: List[asyncio.Queue] = []
        self._pending: List[dict] = []
        self._last_snapshot: Optional[dict] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._server = None
        self._app = None

        # 复用 town 的翻译逻辑(协议键 + 贴图别名 + 坐标回退)
        from .town import TownVisualizer

        self._town = TownVisualizer(alias=self.alias, scenario_dir=self.scenario_dir,
                                    roles=self.roles, nodes_key=nodes_key,
                                    meta_key=meta_key)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> "LiveVisualizer":
        if self._thread is not None:
            return self
        import uvicorn

        app = self.app
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)

        def _run():
            self._server.run()

        self._thread = threading.Thread(target=_run, daemon=True, name="vizkit-live")
        self._thread.start()
        return self

    def url(self) -> str:
        return "http://{}:{}/".format(self.host, self.port)

    def close(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._town.close()

    # ------------------------------------------------------------------
    # 事件入口(引擎线程调用)
    # ------------------------------------------------------------------
    def on_event(self, event: dict) -> None:
        msg = self._town.to_frontend(event)
        if not msg:
            return
        if msg.get("type") == "snapshot":
            self._last_snapshot = msg
        self.broadcast(msg)

    def broadcast(self, msg: dict) -> None:
        """线程安全广播:投递到 uvicorn 事件循环;未启动则进 pending。"""
        loop, clients = self._loop, list(self._clients)
        if loop is None or not clients:
            self._pending.append(dict(msg))
            if len(self._pending) > 2000:
                self._pending = self._pending[-500:]
            return
        for q in clients:
            try:
                asyncio.run_coroutine_threadsafe(q.put(dict(msg)), loop)
            except Exception:
                continue

    def pending(self) -> List[dict]:
        return list(self._pending)

    def drain_pending(self) -> List[dict]:
        out, self._pending = self._pending, []
        return out

    # ------------------------------------------------------------------
    # FastAPI 应用(测试可直接用 TestClient(plugin.app))
    # ------------------------------------------------------------------
    @property
    def app(self):
        if self._app is None:
            self._app = self._build_app()
        return self._app

    def _build_app(self):
        from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse
        from fastapi.staticfiles import StaticFiles
        from fastapi.templating import Jinja2Templates

        app = FastAPI(title="mavis-vizkit 实时可视化(小镇风格)")
        templates = Jinja2Templates(directory=self.template_dir)

        # 角色贴图别名:先挂具体路径,再挂整个 /static
        for role, alias in self.alias.items():
            alias_dir = os.path.join(self.static_root, "assets", "village", "agents", alias)
            if os.path.isdir(alias_dir):
                app.mount("/static/assets/village/agents/" + role,
                          StaticFiles(directory=alias_dir), name="alias-" + alias)
        app.mount("/static", StaticFiles(directory=self.static_root), name="static")

        def _ctx(embed: str) -> dict:
            """页面上下文。embed 非空时模板隐藏浮动面板——供外部平台 iframe 只取场景。"""
            phaser = "/static/vendor/phaser.min.js"
            if not os.path.exists(os.path.join(self.static_root, "vendor", "phaser.min.js")):
                phaser = "https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.js"
            return {
                "persona_names": list(self.init_pos.keys()) or list(self.roles),
                "step": 1,
                "play_speed": 1,
                "zoom": 0,
                "live_mode": True,
                "phaser_src": phaser,
                "embed": embed,
                "stride": self.stride,
                "sec_per_step": self.stride,
                "persona_init_pos": dict(self.init_pos),
                "start_datetime": self.start_datetime,
                "all_movement": {
                    "description": {r: "" for r in (self.init_pos or self.roles)},
                    "conversation": {},
                },
            }

        @app.get("/", response_class=HTMLResponse)
        async def index(request: Request):
            # ?embed=scene 与 /embed/scene 等价:嵌入方不改路径也能只取场景
            return templates.TemplateResponse(
                request, "index.html", _ctx(request.query_params.get("embed", "")))

        @app.get("/embed", response_class=HTMLResponse)
        @app.get("/embed/scene", response_class=HTMLResponse)
        async def embed_scene(request: Request):
            """只取 Phaser 场景(隐藏浮动面板),供外部平台 iframe 引用。

            此前本插件只暴露 `/`,而模板里的 `embed` 变量被写死成 "",
            外部平台因此无法只嵌场景。这里补上嵌入面,并让 `/?embed=scene` 等价。
            """
            return templates.TemplateResponse(request, "index.html", _ctx("scene"))

        @app.get("/health")
        async def health():
            return {"status": "ok", "clients": len(self._clients),
                    "pending": len(self._pending), "roles": self.roles}

        @app.websocket("/ws")
        async def ws_endpoint(ws: WebSocket):
            await ws.accept()
            self._loop = asyncio.get_running_loop()
            q: asyncio.Queue = asyncio.Queue()
            self._clients.append(q)
            hb = asyncio.create_task(self._heartbeat(ws))
            try:
                await ws.send_json({"type": "init", "agents": self.roles,
                                    "textures": {r: self.alias.get(r, r) for r in self.roles}})
                for msg in ([self._last_snapshot] if self._last_snapshot else []) + self.drain_pending():
                    if msg:
                        await ws.send_json(msg)
                while True:
                    await ws.send_json(await q.get())
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                hb.cancel()
                if q in self._clients:
                    self._clients.remove(q)

        return app

    async def _heartbeat(self, ws) -> None:
        try:
            while True:
                await asyncio.sleep(self.ping_interval)
                await ws.send_json({"type": "ping"})
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


register("live", LiveVisualizer)