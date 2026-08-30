# Tiled 地图文件损坏诊断与修复复盘

> 本文记录 2026-08-30 一次 `tilemap.tmx` / `tilemap.json` 反复报"图层数据已损坏"的空错误诊断与最终修复全过程。核心结论是：**以 Tiled 自生成的良构 TMX 为基底做最小修改，而非从 JSON 手写 XML。** 所有图层数据与游戏侧 `tilemap.json` 严格一致。

***

## 1 背景

项目前端地图为 27×24 的村庄地图，共 17 个图层、18 个图块集。正常工作流有两份文件：

* `tilemap.json` — 游戏前端（Phaser）读取，官方格式；

* `tilemap.tmx` — Tiled 编辑器工作底图。

本次遇到的现象是：Tiled 打开 `.tmx` 报 `图层 'Bottom Ground' 数据已损坏 行 83, 列 3`；打开 `.json` 则在弹出框后**无任何附加信息**。

***

## 2 排查过程

### 2.1 现象的二分定位

借助 Tiled 命令行（`tiled --export-map`）与精简对照地图，逐步缩小范围：

| 对照测试                                  | GUI 结果     | 结论                            |
| ------------------------------------- | ---------- | ----------------------------- |
| 官方自带 `orthogonal-outside.tmx`         | 正常打开       | Tiled GUI 环境本身无问题             |
| 极简单图块集地图 `_mini_gui_test.json`        | 同样空报错      | 问题指向 `.json` 与 `.tmx` 共有的某种结构 |
| CLI 导出 `tilemap.json`（`--export-map`） | 成功，输出 57KB | 数据本身 Tiled 能读                 |

关键转机：CLI 导出 `tilemap.json` 写出的 `.json` **保留其原始自生成格式**，用这份 `.json` 转回 `.tmx` 后即可正常打开。这与"手写 XML"形成鲜明对比。

### 2.2 根因

原始的 git 中 TMX 存在三处结构性错误，且相互牵连：

1. **`tilecount="0"`** — 所有图块集（共 18 个）的 `tilecount` 均为 0。原因：原始 JSON 的图片路径指向不存在的 `map_assets/...` 子目录，Tiled 读不到图片尺寸，无法推算 tilecount。
2. **`firstgid`** **重复 / 错误** — 因 tilecount 为 0，Tiled 重新计算 `firstgid`，产生大量重复值（例如 CuteRPG\_Harbor\_C 与 Room\_Builder 都得到 397），破坏了 tile ID 到图块集的映射。
3. **tile ID 错位** — TMX 中的 tile ID 基于错误的 firstgid 计算，与游戏侧 `tilemap.json`（firstgid 正确）映射到不同图块集，导致画面完全错乱。

此外，手写生成 TMX 时若在 CSV 数据块的**最后一行加了尾随逗号**，Tiled 会认为该行列数（28）多于地图宽度（27），从而判定图层数据损坏。

***

## 3 修复步骤（成功做法）

不是从 JSON 重写 XML，而是**先取回 Tiled 自己生成过的良构 TMX 作基底，做 4 处最小修改**：

1. 取 git 中由 Tiled 完整保存过的 `tilemap_44c65a7.tmx` 为基底。
2. 修正图片路径：`map_assets/.../xxx.png` → 平铺文件名 `xxx.png`（图片实际平铺在工作目录）。
3. 用 JSON 中的正确 `firstgid` 与 `tilecount` 覆盖 TMX 中的错误值（让 tile ID 映射与游戏一致）。
4. 用游戏的 JSON tile 数据替换 TMX 中每层 CSV 数据，并**移除每个数据块最后一行的尾随逗号**。

### 3.1 关键脚本

转换脚本 `C:\Users\rui\.trae-cn\work\6a8e467cb67d7b0e3b40c948\`：

* `replace_csv.py` — 用 JSON 数据替换每层 CSV；

* `verify_final.py` — 逐层比对 JSON 与 TMX 的 tile 数据，输出 \[OK]。

### 3.2 验证

* 全部 17 个图层，JSON 与 TMX 数据均为 648 个元素，逐层比对全部 \[OK]；

* `tiled --export-map tilemap.tmx out.json` 成功（exit=0）；

* Tiled GUI 正常打开、可编辑。

***

## 4 经验与教训

* **不要从 JSON 手写/重建 XML。** Tiled 生成的 `.tmx` 有其内部签名（数据块排列、CRLF 换行、图块集声明顺序、`nextlayerid`）。手写 XML 等于再造非标准结构，Tiled 解析时报"图层数据损坏"。从 Tiled 自生成的良构文件上做小修，保留其结构，才能安全加载。

* **CSV 数据块最后一行不能有尾随逗号**，否则列数多 1 触发数据损坏。

* `tilecount` 与 `firstgid` 一旦错误会连锁破坏 tile ID 映射，务必以游戏侧 JSON 为准回填。

* 图片路径要以平铺文件名解析；若原始指向 `map_assets/` 子目录而图片实际平铺，需重写为相对文件名。

* GUI 打开 `.json` 报空错误时，优先怀疑图块集图片（超大纹理、缺失图片）与结构字段，而非数据本身；用 CLI 导出可快速验证数据是否可读。

***

## 5 后续固定工作流

绕开 GUI 打不开 `.json` 的问题：

1. **编辑地图**：始终打开/编辑 `.tmx`（已确认可开）。
2. **转回给前端**：用 CLI 一条命令转回 `.json`：

   ```
   tiled --export-map 输入.tmx 输出.json
   ```
3. 地图内容零损耗，图层、瓦片、图块集全保留。

