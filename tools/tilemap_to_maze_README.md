# tilemap_to_maze: Tiled 地图到 provenance 迷宫数据的无约束转换工具

## 摘要

`tilemap_to_maze.py` 是一个纯命令行工具,将 Tiled 地图编辑器产生的
地图文件(`.tmx` 或导出的 `.json`)转换为 provenance 平台运行时引擎
(`mavisframework.scene.maze`)可直接消费的 `maze.json` 语义地图。

本工具设计目标是替代旧有工具 `tiled_to_maze.py`(GUI 图形界面、假设
旧版 generative_agents 目录结构),消除对运行环境与项目目录的隐含约束:
输入输出路径全部由命令行显式指定,不依赖任何第三方 Python 库
(仅标准库),不假设特定工作目录。

## 目录

1. [背景与动机](#1-背景与动机)
2. [安装与依赖](#2-安装与依赖)
3. [总体流程](#3-总体流程)
4. [命令行用法](#4-命令行用法)
5. [两种语义标注模式](#5-两种语义标注模式)
6. [输出格式与引擎契约](#6-输出格式与引擎契约)
7. [gid 映射表](#7-gid-映射表)
8. [验证方法](#8-验证方法)
9. [已知局限](#9-已知局限)
10. [参考](#10-参考)

---

## 1. 背景与动机

provenance 平台的场景由两类文件描述:

- `tilemap.json`:Phaser 前端渲染使用的完整地图(图层、贴图、碰撞),
  由 Tiled 导出;
- `maze.json`:引擎逻辑层使用的语义地图(各格子的空间地址层级),
  驱动 BFS 寻路与空间检索。

`maze.json` 与 `tilemap.json` 是同一场景的两个视图:前者描述"哪些格子
属于哪个房间/区域/对象",后者描述"这些格子画成什么样"。本工具负责从
Tiled 的语义标注生成 `maze.json`。

旧工具 `tiled_to_maze.py` 的局限:

| 局限 | 后果 |
|---|---|
| GUI 界面(tkinter),手动点选文件 | 无法脚本化、难以纳入 CI/交接流程 |
| 假设旧版 generative_agents 目录结构 | 与 provenance + mavis 的路径布局不兼容 |
| 只输出数据层,无 provenance 特定契约 | 输出需二次加工才能被引擎消费 |
| 无房间级语义标注手段 | 生成的地址停留在区域类型粒度 |

本工具逐一解决上述问题。

## 2. 安装与依赖

运行环境要求:

- Python 3.6 及以上(无第三方库依赖,全部使用标准库);
- Tiled 1.4 及以上(仅当输入为 `.tmx` 时用于导出 JSON;
  直接输入 `.json` 则不需要)。

Tiled 下载地址:https://thorbjorn.itch.io/tiled

## 3. 总体流程

```
Tiled 编辑地图(.tmx)
        |
        v
tilemap_to_maze.py 地图.tmx -o maze.json --tiled <Tiled路径>
        |  (内部: tiled --export-map json → .exported.json)
        v
maze.json(引擎消费)
```

流程要点:编辑阶段使用 Tiled 原生格式 `.tmx`,转换阶段工具自动调用
Tiled 命令行完成 `.tmx → .json` 导出,使用者无需手动执行导出操作。

## 4. 命令行用法

### 4.1 输入为 Tiled 导出的 JSON

```bash
python tilemap_to_maze.py tilemap.json -o maze.json
```

### 4.2 输入为 .tmx(自动导出 JSON)

```bash
python tilemap_to_maze.py map.tmx -o maze.json \
    --tiled "C:/Program Files/Tiled/tiled.exe"
```

`--tiled` 指定 Tiled 可执行文件路径;未指定时尝试从系统 PATH 查找。

### 4.3 对象层模式(推荐,见第 5 节)

```bash
python tilemap_to_maze.py map.tmx -o maze.json \
    --room-layers "Rooms" \
    --sector-rect-layers "Sectors" \
    --tiled "C:/Program Files/Tiled/tiled.exe"
```

### 4.4 gid 模式两步流程(兼容现有标注)

```bash
# 第一步:生成 gid 映射表(名称待填写)
python tilemap_to_maze.py tilemap.json --auto-gid-map -o gid_map.json --world "the Ville"

# 第二步:编辑 gid_map.json 填写名称后转换
python tilemap_to_maze.py tilemap.json -o maze.json --gid-map gid_map.json
```

### 4.5 参数总览

| 参数 | 默认值 | 说明 |
|---|---|---|
| `input` | 必填 | 输入地图文件(`.tmx` 或 `.json`) |
| `-o, --output` | `maze.json` | 输出文件路径 |
| `--tiled` | `tiled` | Tiled 可执行文件路径(仅 `.tmx` 输入时需要) |
| `--world` | `the Ville` | 世界名(引擎地址层级的第一层) |
| `--sector-layers` | `Sector Blocks` | gid 模式:区域标注图层名 |
| `--arena-layers` | `Arena Blocks` | gid 模式:场所标注图层名 |
| `--object-layers` | `Object Interaction Blocks` | gid 模式:对象标注图层名 |
| `--gid-map` | 无 | gid 映射表 JSON 路径(名称已填写) |
| `--auto-gid-map` | 关 | 仅生成 gid 映射表,不执行转换 |
| `--room-layers` | 无 | 对象层模式:场所矩形对象所在图层 |
| `--sector-rect-layers` | 无 | 对象层模式:区域矩形对象所在图层 |

## 5. 两种语义标注模式

Tiled 提供两种语义标注手段,本工具分别对应两种生成模式。

### 5.1 对象层模式(推荐)

原理:在 Tiled 中新建对象层(Object Layer),用矩形对象覆盖每个房间,
对象名称即房间名称。工具读取矩形覆盖的格子生成地址层级。

操作步骤(以 Tiled 为例):

1. 新建对象层,命名为 `Sectors`;
2. 在 `Sectors` 层画一个矩形覆盖整个"交易大厅",对象名填 `Trading Floor`;
3. 新建对象层 `Rooms`,在 `Trading Floor` 内画矩形覆盖
   `Market Screen`、`Podium`、`Seat` 等对象,对象名即对象名;
4. 运行转换命令(见 4.3)。

生成的地址结构:`世界 → Sectors 矩形名 → Rooms 矩形名`。

优势:所见即所得,对象名即地址名,不受 gid 粒度限制,可精确到房间与
对象级;相邻房间共用贴图块时仍能正确区分。

### 5.2 gid 模式(兼容现有标注)

现有 `tilemap.json` 使用图块层(tilelayer)做语义标注:

- `Sector Blocks`:不同 gid 表示不同区域;
- `Arena Blocks`:不同 gid 表示不同场所;
- `Object Interaction Blocks`:不同 gid 表示不同对象。

同一图层内,每个不同的 gid 视为一个语义类型。工具按 gid 将格子分组,
并通过 gid 映射表(gid_map.json)将 gid 映射为可读名称。

局限:gid 粒度由标注决定。当多个相邻房间共用同一 gid 时(例如走廊与
休息室),gid 模式无法自动细分,需要对象层模式或人工补充。

## 6. 输出格式与引擎契约

`maze.json` 顶层结构:

```json
{
  "world": "the Ville",
  "tile_size": 32,
  "size": [24, 27],
  "map": {"width": 27, "height": 24},
  "camera": {"start_x": 0, "start_y": 0},
  "tile_address_keys": ["world", "sector", "arena", "game_object"],
  "tiles": [
    {"coord": [4, 4], "address": ["Trading Center"]},
    {"coord": [5, 4], "address": ["Trading Center", "Trading Floor"]},
    {"coord": [5, 5], "address": ["Trading Center", "Trading Floor", "Market Screen"]}
  ]
}
```

与引擎的契约要点:

- `world`:世界名,引擎构造 Tile 时作为地址首项;
- `size`:`[height, width]`,与 `Maze(config["size"])` 的预期一致;
- `tiles[].coord`:`[x, y]`,与 `size` 的宽高对应;
- `tiles[].address`:语义地址,**不含 world 前缀**——引擎在
  `Tile.__init__` 中自动执行 `[world] + address`。若 address 含 world
  会导致世界名重复,地址层级错乱;
- `tile_address_keys`:地址层级键名,默认
  `["world", "sector", "arena", "game_object"]`;
- 碰撞信息不在本工具输出范围内,由 Phaser 前端的 `Collisions` 图层处理。

## 7. gid 映射表

`--auto-gid-map` 生成的映射表结构:

```json
{
  "world": "the Ville",
  "sector": {
    "32146": {"name": "SECTOR_32146", "tiles": 288},
    "32177": {"name": "SECTOR_32177", "tiles": 39}
  },
  "arena": {
    "32181": {"name": "ARENA_32181", "tiles": 76}
  },
  "object": {
    "32240": {"name": "OBJECT_32240", "tiles": 2}
  }
}
```

`name` 字段为占位符,需人工填写为真实名称(例如
`SECTOR_32146` 改为 `Trading Center`)。`tiles` 字段为该 gid 覆盖的
格子数,供命名时参考,转换时忽略。

## 8. 验证方法

转换完成后,建议执行以下验证:

1. 结构校验:确认 `size`、`map`、`tiles` 字段与输入地图一致;

   ```bash
   python -c "import json; m=json.load(open('maze.json',encoding='utf-8')); \
   print('size', m['size'], 'tiles', len(m['tiles'])); \
   print('distinct addresses', len(set(tuple(t['address']) for t in m['tiles'] if t['address'])))"
   ```

2. 与既有 `maze.json` 对比(换场景不改语义时):

   ```bash
   python -c "
   import json
   from collections import defaultdict
   def am(p):
       d=defaultdict(list)
       for t in json.load(open(p,encoding='utf-8'))['tiles']:
           a=t.get('address',[])
           if a: d[tuple(a)].append(tuple(t['coord']))
       return d
   o,n=am('旧maze.json'),am('新maze.json')
   common=set(o)&set(n)
   print('common',len(common),'identical',sum(1 for k in common if o[k]==n[k]))
   "
   ```

3. 运行验证:在 provenance 中启动 `/embed/scene`,确认角色能从出生点
   移动到各房间(引擎 BFS 寻路依赖地址连通)。

## 9. 已知局限

- **gid 粒度**:gid 模式受标注粒度限制,相邻房间共用 gid 时无法细分,
  需使用对象层模式;
- **对象层重叠**:对象层模式中,若多个矩形重叠,先声明的矩形优先;
  建议设计时避免重叠;
- **仅支持正交地图**:工具基于 `tilewidth/tileheight` 与正交网格假设,
  未测试等距/六边形地图;
- **碰撞图层不处理**:碰撞信息由前端 Phaser 处理,本工具不涉及。

## 10. 参考

- Tiled 官方文档:https://doc.mapeditor.org/
- 引擎消费点:`mavisframework/scene/maze.py`(寻路与空间检索),引擎仓库
  [hellobs/mavis](https://github.com/hellobs/mavis)
- 平台仓库:[hellobs/provenance](https://github.com/hellobs/provenance)
  (本工具随平台 `tools/` 目录分发)
- 前端渲染:`provenance/frontend/templates/main_script.html`(preload/create)
- 角色/场景配置工具:引擎仓库 `config_tool/`(端口 5002,表单生成
  agent.json/relationships.json/story.json)
- Unity 版前端(已冻结):[hellobs/Multi-Model-AI-Visualization-and-Interactive-Simulation-Platform](https://github.com/hellobs/Multi-Model-AI-Visualization-and-Interactive-Simulation-Platform)
- 旧工具(参考):https://github.com/jiejieje/tiled_to_maze
