# tools/ —— 仓根工具(整仓生命周期)

> **状态**:现行
> **最后核对**:2026-10-08
> **说明**:仓根 tools/ 与内层 provenance/tools/ 的分工(装新机/起面/打包/搬迁 vs 体检/探针/审计)

## 为什么有两处 `tools/`

**分工是真的,名字没表达出来**,所以这里写清楚:

- **本目录(仓根)放"管整个仓"的工具** —— 它们要操作 `packages/`、`tests/`、CI 与交付包,
  所以必须在仓根,**不能塞进内层**;
- **`provenance/tools/` 放"贴应用内部数据"的体检与探针**(见那里的 README)。

时间线也印证了这点:内层 `provenance/docs`、`provenance/` 生于 2026-08-25(应用先有),
本目录与 `tests/` 生于 2026-08-30(整仓工程化开始),内层 `provenance/tools/` 生于 2026-09-19(体检工具成体系)。

## 本目录有什么

| 文件 | 干什么 |
|---|---|
| `setup_all.py` | **新机一条命令**:引擎 + venv + 依赖 + Ollama 模型 + 自检 |
| `serve_all.py` | **一条命令起/查/停各个面**(5010/5002/5003/5020/8060) |
| `make_demo_zip.py` | 重打交付包 zip + 写 sidecar(冻结流程第 1 步) |
| `migrate_data_layout.py` | 数据归类搬迁:把"跑出来的存档"从代码目录挪到仓根 `data/`(**默认 dry-run**,台账可回滚) |
| `install_scene.py` | 把 Tiled 里做好的新场景装进平台(导出 JSON + 换场景 + 重生成 maze.json) |
| `tilemap_to_maze.py` | Tiled 地图 → `maze.json` 转换(说明见 `tilemap_to_maze_README.md`,`gid_map_tilemap2.json` 是映射表) |
| `check_engine_baseline.py` | 断言已安装的 `mavisframework` 具备 case01 需要的通用插件面 |
| `run_live_watchdog.ps1` | 实时面看护进程(掉线重起,日志进 `logs/`) |

## 常敲的命令(都在仓根执行)

```powershell
cd D:\zzr\provenance
.venv-live\Scripts\python.exe tools\serve_all.py --status      # 谁在跑
.venv-live\Scripts\python.exe tools\serve_all.py               # 起全部面
.venv-live\Scripts\python.exe tools\serve_all.py --stop        # 停(只停本工具起过的)
```

> 注意:根目录**只有 `tools/`**,不再有 `results/`(那是历史空壳,已删)。
> 数据(存档/状态/登记簿)统一在仓根 `data/`,见 `provenance/docs/仓库布局说明.md`。
