# tilemap_to_maze.py — 无约束的 Tiled 地图 → provenance maze.json 转换工具

> 与 `tiled_to_maze.py`(旧工具)的区别:
> 旧工具 = GUI(tkinter)+ 假设旧 generative_agents 目录结构 + 只做数据层转换。
> 本工具 = **纯 CLI、零 GUI、零目录假设、输入输出由命令行完全指定**。

## 一、完整流程(接手人只需 2 个命令)

接手人在 **Tiled 里编辑 `.tmx`**(Tiled 原生格式),工具自动完成后续:

```
1. 编辑地图:  Tiled 打开 .tmx → 画房间/对象 → 保存 .tmx
2. 转换一次:  python tilemap_to_maze.py 地图.tmx -o maze.json --tiled "C:/Program Files/Tiled/tiled.exe"
   (自动: tmx → json 导出 → 生成 maze.json)
```

工具检测到输入是 `.tmx` 时,自动调用 Tiled 命令行导出 json,无需手动点"导出"。

## 二、安装要求

- Python 3.6+(无需任何第三方库,纯标准库)
- Tiled(https://thorbjorn.itch.io/tiled)—— 仅当输入是 .tmx 时需要(用于导出 json);
  直接给 .json 输入则不需要 Tiled

## 三、用法

```bash
# 1) 输入是 Tiled 导出的 .json(已在 Tiled 里手动导出过)
python tilemap_to_maze.py tilemap.json -o maze.json

# 2) 输入是 .tmx(自动调用 Tiled 导出 json)
python tilemap_to_maze.py map.tmx -o maze.json --tiled "C:/Program Files/Tiled/tiled.exe"

# 3) 首次:生成 gid 映射表(名称待填)
python tilemap_to_maze.py tilemap.json --auto-gid-map -o gid_map.json
#    编辑 gid_map.json,把 SECTOR_x/ARENA_x/OBJECT_x 改成真实房间名,再:
python tilemap_to_maze.py tilemap.json -o maze.json --gid-map gid_map.json

# 4) 对象层模式(推荐,见第四节):用矩形对象标注房间
python tilemap_to_maze.py map.tmx -o maze.json --room-layers "Rooms" --sector-rect-layers "Sectors" --tiled "C:/Program Files/Tiled/tiled.exe"
```

### 参数说明

| 参数 | 默认 | 说明 |
|---|---|---|
| `input` | 必填 | Tiled 地图文件(.tmx 或 .json) |
| `-o/--output` | `maze.json` | 输出路径 |
| `--tiled` | `tiled` | Tiled 可执行文件路径(.tmx 输入时用) |
| `--sector-layers` | `Sector Blocks` | gid 模式:区域标注图层名 |
| `--arena-layers` | `Arena Blocks` | gid 模式:场所标注图层名 |
| `--object-layers` | `Object Interaction Blocks` | gid 模式:对象标注图层名 |
| `--gid-map` | 无 | gid→名称映射 JSON |
| `--auto-gid-map` | 关 | 只生成 gid 映射表(不转换) |
| `--room-layers` | 无 | 对象层模式:场所矩形对象层 |
| `--sector-rect-layers` | 无 | 对象层模式:区域矩形对象层 |

## 四、两种标注模式(如何告诉工具"哪个格子是哪个房间")

### 模式 A:对象层(推荐,最直观)

在 Tiled 里新建**对象层(Object Layer)**,画**矩形对象**覆盖每个房间:

```
对象层 "Sectors":  画一个矩形覆盖"交易大厅",对象名填 "Trading Floor"
对象层 "Rooms":    画矩形覆盖各细分场所,对象名填 "Market Screen"/"Podium"...
```

工具按矩形覆盖的格子生成地址:`世界 → Sector矩形名 → Room矩形名`。
对象名就是房间名,所见即所得,接手人不需要懂 gid。

```bash
python tilemap_to_maze.py map.tmx -o maze.json --room-layers "Rooms" --sector-rect-layers "Sectors" --tiled "..."
```

### 模式 B:gid 标注(兼容现有 tilemap.json 的机制)

现有 `tilemap.json` 用图块层标注:Sector Blocks / Arena Blocks / Object Interaction Blocks,
**同一图层内不同 gid = 不同区域类型**。

```bash
# 首次生成映射表:
python tilemap_to_maze.py tilemap.json --auto-gid-map -o gid_map.json
# 编辑 gid_map.json 填名称(如 SECTOR_32146 → "Trading Center")后:
python tilemap_to_maze.py tilemap.json -o maze.json --gid-map gid_map.json
```

模式 B 粒度较粗(同一 gid 覆盖多个房间),适合"区域类型级";要精确到房间建议用模式 A。

## 五、输出格式(provenance 引擎契约)

```json
{
  "world": "the Ville",
  "tile_size": 32,
  "size": [24, 27],
  "map": {"width": 27, "height": 24},
  "camera": {"start_x": 0, "start_y": 0},
  "tile_address_keys": ["world", "sector", "arena", "game_object"],
  "tiles": [
    {"coord": [4, 4], "address": ["the Ville", "Trading Center"]},
    {"coord": [5, 4], "address": ["the Ville", "Trading Center", "Trading Floor"]}
  ]
}
```

- 引擎 `Maze(config)` 消费 `size/tile_size/tiles`,BFS 寻路基于有 address 的格子
- 碰撞由前端 Phaser 的 `Collisions` 图层处理,不由本工具生成

## 六、验证

转换后建议对照现有 `maze.json` 检查:
```bash
python -c "import json;m=json.load(open('maze.json',encoding='utf-8'));print('size',m['size'],'tiles',len(m['tiles']));print('地址数',len(set(t['address'][-1] for t in m['tiles'] if len(t['address'])>1)))"
```
并在 provenance 里跑 `/embed/scene` 确认角色能寻路。
