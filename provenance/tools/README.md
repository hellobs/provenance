# provenance/tools/ —— 内层工具(体检 / 探针 / 审计)

> **状态**:现行
> **最后核对**:2026-10-08
> **说明**:内层 tools/ 的体检与探针工具清单(含与仓根 tools/ 的边界)

## 边界(与仓根 `tools/` 的分工)

- **本目录**:贴**应用内部数据**干活的工具 —— 读存档/记录/前端模板/WS 消息,做体检与取证。
  它们默认在 `provenance/` 下运行(`cd provenance`),因为要按包内相对位置找代码与模板;
- **仓根 `tools/`**:管**整仓生命周期**(装新机、起全部面、打交付包、搬迁数据)—— 见 `../../tools/README.md`。

> 归位约定(2026-10-08):内层**根目录**只放两个门面 `live_switch.py` / `live_fastapi.py`,
> 其它探测/体检脚本一律放本目录(`hc_check.py`/`hc_tile.py`/`hc_ws.py` 已按此归位)。

## 本目录有什么

**一次跑完的总检**

| 文件 | 干什么 |
|---|---|
| `deep_check.py` | 深度体检:一次跑完所有维度,输出紧凑的 ✓/⚠ 清单(冻版晨检用) |
| `hc_data_integrity.py` | 数据完整性(governance.json / tilemap / 模拟存档 / maze / agent) |
| `hc_static_quality.py` | 静态质量:硬编码敏感信息、死代码/TODO/残留引用、闲置资源 |
| `doc_audit.py` | 文档体检 + **文档索引生成**(`--banner --index`);状态块守卫在这里 |

**前端 / 模板 / 端点**

| 文件 | 干什么 |
|---|---|
| `hc_frontend_endpoints.py` | 抓前端 fetch/XHR/WebSocket 端点,供与后端路由比对 |
| `hc_template_render.py` | 校验 index.html 模板在 case00/case01 各 embed 下渲染、面板显隐与 div 配平 |
| `hc_maze_usage.py` | 前端 maze.json 是否在运行时真被引用(还是静态遗留) |
| `align_frontend_maze.py` / `fix_maze_collision.py` | 迷宫对齐与修复(补墙格 / 对齐 Collisions 图层) |

**实时面(5010)探针**

| 文件 | 干什么 |
|---|---|
| `embed_probe.py` | 嵌入面/数据面的跨源与可嵌入性,逐项出证据 |
| `hc_ws.py` / `hc_ws_probe.py` / `hc_ws_live_probe.py` | 连 `/ws` 收消息,判断模拟是否真在推进、路径是否合法 |
| `hc_tile.py` | tilemap.json 的 Collisions 图层是否被用作阻挡 |
| `hc_check.py` / `hc_panel_ctx.py` / `hc_panel_refs.py` | 面板上下文与引用的小探针(无 docstring,历史取证用) |

**交付与配置**

| 文件 | 干什么 |
|---|---|
| `verify_demo_sync.py` | **服务读的那份**与**交付包里的那份**必须逐字节相同(`--require-zip` 连 zip 一起比) |
| `setup_api.py` | 一条命令配好外部 API key(含 Router 用的独立模型),并联网自检 |

## 常敲的命令

```powershell
cd D:\zzr\provenance\provenance
.venv-live\Scripts\python.exe tools\deep_check.py                  # 深度体检
.venv-live\Scripts\python.exe tools\doc_audit.py --banner --check   # 文档状态块守卫(应为 0)
.venv-live\Scripts\python.exe tools\verify_demo_sync.py --require-zip   # 交付件四者一致
```
