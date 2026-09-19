# -*- coding: utf-8 -*-
"""实时可视化插件:把运行中的事件**边跑边推**给前端(不是回放)。

复用点
------
- 页面:由调用方传入 `static_root` / `template_dir`(前端资源根);
  角色的贴图别名经 alias 挂载到既有目录(只影响贴图);
- 快照:新连入的客户端先收 `init` + 当前 `snapshot`,随后接收实时事件。
  若调用方从不发 `snapshot` 事件,则用"每个角色最近一条状态"**合成**一份
  (见 `catch_up`),否则中途/事后打开页面的人会看到空小镇而不知道原因。
- 结束:调用方跑完后应调 `finish()` 发一条 `done`;新连入者也会立刻收到
  `done`,从而知道"推演已结束、服务只是在保持"。

实现要点
--------
- uvicorn 跑在守护线程里,插件本身在引擎线程被调用 → 事件通过
  `asyncio.run_coroutine_threadsafe` 投递到 uvicorn 的事件循环;
- 服务器未启动时事件进 pending 缓冲(便于单测与无头运行,不丢语义);
- **不静默**:推送失败、连接异常、心跳异常都记日志(不打断引擎,但必须留痕)。
  宁可日志吵,也不要"页面上什么都没有、日志里也什么都没有"。
"""
import asyncio
import logging
import os
import threading
from typing import Dict, List, Optional

from .. import Visualizer, register
from .town import scenario_coords

log = logging.getLogger("mavis_vizkit.live")


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
        self._last_agents: dict = {}      # 角色名 -> 最近一条 agent 前端消息(用于合成追赶快照)
        self._last_time: str = ""         # 最近一次模拟时间(同上)
        self._finished: bool = False      # 运行是否已结束(由 finish() 或 done 事件置位)
        self._finish_reason: str = ""     # 结束原因(随 done 一起告诉客户端)
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
        kind = msg.get("type")
        # 模拟时间:任何带 time 的消息都算(agent/chat_line 也带),
        # 否则"只收到过 agent 就被中断的运行"合成快照时时间是空的。
        if msg.get("time"):
            self._last_time = msg["time"]
        if kind == "agent":
            # 记下每个角色的最近状态:新连接要靠它合成"当前状态"快照。
            name = msg.get("name")
            if name:
                self._last_agents[name] = dict(msg)
        elif kind == "snapshot":
            self._last_snapshot = msg
        elif kind == "done":
            self._finished = True
            self._finish_reason = msg.get("reason", "") or self._finish_reason
        self.broadcast(msg)

    @property
    def finished(self) -> bool:
        """运行是否已结束(前端"已结束"指示与服务端 done 的依据)。"""
        return self._finished

    @property
    def finish_reason(self) -> str:
        return self._finish_reason

    def finish(self, reason: str = "run_finished") -> None:
        """运行结束:广播一条 `done`,并记住状态以便后到的人也知道。

        为什么必须发:前端有 `done` 的处理分支,但如果没人发,页面就只是"人不动、
        也没人告诉你为什么"——用户实测反馈过。`--hold` 只保持服务,不代表还在跑。
        """
        if self._finished and reason == self._finish_reason:
            return
        self._finished = True
        self._finish_reason = reason
        self.broadcast({"type": "done", "reason": reason})

    def catch_up(self) -> Optional[dict]:
        """新连接的"当前状态"追赶:优先给真实 snapshot,否则用各角色最近一条状态合成。

        为什么必须有它:前端只在收到 `snapshot` 时才会立刻把角色**归位**
        (`applySnapshot`),否则要等后续逐条 `agent` 消息才知道角色在哪。
        而在"跑完之后服务保持(--hold)"或"中途才打开页面"这两种常见情形下,
        已经没有后续消息了——于是页面是一个**空小镇**,看起来像坏了。
        2026-09-19 用户实测反馈:"左边那个可视化里面的人根本没反应"。

        合成快照只带前端归位与名牌需要的字段(coord / action / location),
        形状与 `applySnapshot` 的读法一致;拿到真实 snapshot 时仍以真实那份为准。
        """
        if self._last_snapshot:
            return self._last_snapshot
        if not self._last_agents:
            return None
        agents = {}
        for name, msg in self._last_agents.items():
            agents[name] = {
                "coord": msg.get("coord"),
                "texture": msg.get("texture"),
                "action": msg.get("action", ""),
                "location": msg.get("location", ""),
                "currently": msg.get("currently", ""),
                "role_type": msg.get("role_type", "user"),
            }
        return {"type": "snapshot", "agents": agents, "time": self._last_time,
                "synthesized": True}

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
                # 单个客户端推送失败不影响其它客户端,但**必须留痕**——
                # 否则表现就是"某个人页面上什么都没有",而日志里也什么都没有。
                log.warning("向一个客户端推送失败(type=%s)", msg.get("type"), exc_info=True)

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
                # 先给"当前状态"追赶,再补没送到过的事件。
                catch = self.catch_up()
                if catch:
                    await ws.send_json(catch)
                for msg in self.drain_pending():
                    if msg:
                        await ws.send_json(msg)
                # 已经跑完才连进来的人:立刻告诉他"结束了",否则页面只是静悄悄的。
                if self._finished:
                    await ws.send_json({"type": "done",
                                        "reason": self._finish_reason or "run_finished"})
                log.info("客户端接入(当前 %d 个,追赶快照=%s,已结束=%s)",
                         len(self._clients), bool(catch), self._finished)
                while True:
                    await ws.send_json(await q.get())
            except WebSocketDisconnect:
                pass
            except Exception:
                # 断线是常态,但"不是断线"的异常必须留痕,否则前端黑屏无从排查。
                log.warning("websocket 连接异常关闭", exc_info=True)
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
            # 心跳失败基本等于这条连接死了;记一行日志,便于对照"页面为什么停住"。
            log.debug("心跳终止(连接已关闭)", exc_info=True)


register("live", LiveVisualizer)