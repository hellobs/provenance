# mavis 插件面合入 main + bump 到 1.2.0 —— 合并 bump 预演（只出证据，不执行）

> **状态**:历史存档
> **最后核对**:2026-09-21
> **说明**:预演材料;bump 已落地(v1.2.0/1.2.1 已 tag)
> **最后核对**:2026-09-21
> **说明**:预演材料;bump 已落地(v1.2.0/1.2.1 已 tag)

> 2026-09-18 · 在临时克隆里走通整条链路并取证，未动真 mavis 仓库（`D:\zzr\mavis`）任何状态。
> 本文件是"这条路能否按决策材料执行"的实证，供人工拍板。

## 0. 预演范围与绝不触碰的边界

预演在临时克隆 `D:\zzr\drill_tmp\mavis_dryrun` 内进行：把 `feat/plugin-surface` 快进合并到
克隆的 main → bump `1.1.0 → 1.2.0` → 构建 sdist + wheel → 解包断言 → 跑全量测试 →
隔离 venv 验证 CI 前置断言。真 mavis 分支、tag、工作树、`dist/` 全程不动；前后对比见第 5 节。

## 1. 预演链路（每步命令 + 输出摘要）

### 1.1 克隆 feat/plugin-surface 到临时目录

```
cd D:\zzr
git clone --branch feat/plugin-surface D:\zzr\mavis D:\zzr\drill_tmp\mavis_dryrun
# 克隆后默认 HEAD = dca75b6(feat/plugin-surface),无本地 main
```

### 1.2 在克隆里建本地 main 并快进合并

```
git checkout -b main origin/main          # main 定位到 8f12919
git merge --ff-only feat/plugin-surface
# Updating 8f12919..dca75b6  Fast-forward
# 8 files changed, 748 insertions(+), 32 deletions(-)
#  create mode 100644 mavisframework/plugin.py
#  create mode 100644 tests/test_plugin_surface.py
# main 现在 = dca75b6(与决策材料 §1/§2 的领先 2、可快进结论一致)
```

快进合并成功，无冲突、无 merge commit 必要，实证了决策材料 §2 的"win"(可 fast-forward)判定。

### 1.3 bump 1.1.0 → 1.2.0

改动四处（版本引用全部对齐）：

- `pyproject.toml` 第 7 行 `version = "1.2.0"`
- `mavisframework/__init__.py` 第 61 行 `__version__ = "1.2.0"`
- `uv.lock` 第 183 行 mavisframework 包 `version = "1.2.0"`
- `README.md` / `README_zh.md` 的构建示例文件名与语义化版本示例 `1.2.0`

### 1.4 构建 sdist + wheel（无联网）

本机 `.venv-live` 未装 `build` 模块；为遵守"避免联网装构建依赖"，直接调用
`setuptools.build_meta` 的 `build_sdist` / `build_wheel` 构建（等价 `python -m build --no-isolation`）：

```
D:\zzr\provenance\provenance\.venv-live\Scripts\python.exe -c "import setuptools.build_meta as bm; print(bm.build_sdist('dist')); print(bm.build_wheel('dist'))"
```

产物（输出摘要）：

- sdist: `mavisframework-1.2.0.tar.gz`（120381 字节）
- wheel: `mavisframework-1.2.0-py3-none-any.whl`（112844 字节）

构建日志已可见 `adding 'mavisframework/plugin.py'`，说明显式 `packages` 白名单的顶层包内模块
确实被包含进 wheel。

### 1.5 解包断言用的命令

```
tar -xzf dist/mavisframework-1.2.0.tar.gz -C dist                       # sdist 解包到 mavisframework-1.2.0/
python -c "import zipfile; zipfile.ZipFile('dist/mavisframework-1.2.0-py3-none-any.whl').extractall('dist/whl_x')"   # wheel 解包
```

## 2. 产物断言（四条，全部通过）

对 sdist(`mavisframework-1.2.0/`) 与 wheel(`whl_x/`) 两份产物逐条 grep / 计数：

| 断言 | 检验 | sdist 结果 | wheel 结果 |
|---|---|---|---|
| 1 | `runtime/simulator.py` 含 `external_state` / `interaction_request` | external_state 8 / interaction_request 8 | 与 sdist 一致 |
| 2 | `core/agent_core.py` 含 `role_directive` / `subscribe_chat_line` | role_directive 2 / subscribe_chat_line 4 | 与 sdist 一致 |
| 3 | **`plugin.py` 存在于两份产物**（本轮从未验证的关键点） | 存在,8183 字节 | 存在,8183 字节 |
| 4 | 版本元数据为 1.2.0 | PKG-INFO `Version: 1.2.0` | METADATA `Version: 1.2.0` |

最关键的第三条已有铁证：这是 pyproject 用显式 `packages` 白名单后"新增顶层包内模块是否真进制品"
此前只靠推理的一处——现在解包确认两份产物都含 `mavisframework/plugin.py`，且 wheel 构建日志
明确 `adding 'mavisframework/plugin.py'`。

产物哈希（预演唯一副本，供对照，不入真仓库）：

- sdist SHA256 `2DB2C4611CA9711A2F66CE304D56E5E206B9E4135D1B25CC4EB39A372FC9C58B`
- wheel SHA256 `5DB88742FD5AC607FCB7982159B850108140C17C74450258067BD64FE1D19FF1`

## 3. 测试（克隆里 mavis 全量）

```
cd D:\zzr\drill_tmp\mavis_dryrun
D:\zzr\provenance\provenance\.venv-live\Scripts\python.exe -m pytest -o addopts="" -q
# 122 passed in 3.92s
```

合并 + bump 后 122 全绿，与决策材料 §5 所说 mavis 122 一致，无回归。

## 4. CI 前置断言的脚本可行性验证（隔离 venv）

另建临时 venv `D:\zzr\drill_tmp\ci_venv`（未污染 `.venv-live`），装刚构建的 1.2.0 wheel
（非 editable，走 site-packages），然后跑三条断言：

1. `import mavisframework.plugin` 成功（模块位于 site-packages 内）✓
2. `Simulator.__init__` 签名含 `plugins` 参数 ✓（`inspect.signature` 参数列表出现 `plugins`）
3. `agent_core.subscribe_chat_line` 存在且可调用 ✓

`mavisframework.__version__ == "1.2.0"`，import 路径确在 `Lib\site-packages`。

结论：决策材料 §3 建议 CI 加的这条前置断言"写得出来、能过"，且关键点是**先建置后探测**
——即便将来有人误装回不含插件面的旧制品，该断言在 import 阶段就会红，能拦住容器错装。

## 5. 真 mavis 仓库未动的证据（预演前后对比）

| 检验项 | 预演前 | 预演后 | 一致 |
|---|---|---|---|
| HEAD | `dca75b6` | `dca75b6` | ✓ |
| main | `8f12919` | `8f12919` | ✓ |
| origin/main | `8f12919` | `8f12919` | ✓ |
| tag 列表 | `v1.0-goals-internal` | `v1.0-goals-internal` | ✓ |
| 工作树 | clean（无未提交改动） | clean | ✓ |
| dist/ | 仅 1.0.0 + 1.1.0 两份旧制品 | 不变（时间戳 08/25、09/18 19:16 未变） | ✓ |

真 mavis 的 `HEAD` 仍是 `feat/plugin-surface` 上未合入 main 的 `dca75b6`，main/origin/main 仍是
`8f12919`，未打 `v1.2.0` tag，工作清洁，`dist/` 无本次产物混入。全程没在真仓库目录构建或产生 dist。

预演用临时克隆 `D:\zzr\drill_tmp\mavis_dryrun` 与临时 venv `D:\zzr\drill_tmp\ci_venv` 在结束后删除，
并已确认删除（见本报告交付确认；若未删，属回滚需要，非真仓库状态）。

## 6. 小项

### 6.1 OpenAPI YAML 严格度不一致

`Issue` 用了 `additionalProperties: false` + `required` + `risk` 枚举（严格），而
`/api/runs/{run_id}` 的内联 `RunDetail` schema 顶层未声明 required / 未闭合（宽松）。二选一：
**保留二者不同的严格度**，BUG 不改实现，但已在 `case01_api.openapi.yaml` 的 RunDetail 顶层
加一行注释说明理由——RunDetail 是只读聚合展示面，顶层刻意不设 `additionalProperties:false`
以容纳对接确认单允许的"仅追加 add-only"扩展；而 Issue 是平台建单/幂等契约，必须闭合。该约束
已在文件内成文，YAML 仍可正常解析。

### 6.2 把踩坑写进转接文档

呼应本轮在 OpenAPI 漂移守卫红态验证时用 `git checkout` 还原整文件导致回退同轮未提交改动的坑，
已在 `转接_给下一棒_20260918.md` 第 9 节工作约定里新增一条：验证失败场景时不要用
`git checkout` 还原整个文件，只"删一段验证 → 把那段加回"或做纯内存比较。

## 7. 结论

按决策材料执行这条链路（快进合并 plugin-surface → bump 1.2.0 → 重建制品 → 解包校验 →
打 tag v1.2.0 → provenance 更新 pin）没有暴露出新的阻塞问题：

- 快进合并无冲突，037 家二次确认 main 仍是 `8f12919`、可 `--ff-only`；
- 版本引用（pyproject / __init__ / uv.lock / README）四处全部同步改，无遗漏引用导致产物版本错配；
- 显式 `packages` 白名单下新增的 `plugin.py` **确实进产物**（sdist 与 wheel 双份都含），
  此前"只靠推理"的假设已被解包证实，是本次预演最重要的实证；
- 122 全测通过、CI 三条前置断言隔离 venv 可写出可过。

一个沿用注意点（非新问题，重述 bump 方案的既有纪律）：构建时用的 Python 是
`D:\zzr\provenance\provenance\.venv-live\Scripts\python.exe`（带 setuptools 的干净解释器），执真实
主线时若在真仓库目录里跑同命令会生成未经版本管理的 `build/`、`*.egg-info`，属预期临时产物；
正式 bump 落地时记得在非源码目录或至少确认产物校验后再提交。

## 8. 下一棒要踩的坑（新增 / 仍存在）

- `build` 模块在本机 `.venv-live` 未装：构建不能直接 `python -m build`，要用
  `setuptools.build_meta` 直接调 `build_sdist/build_wheel`（等价 `--no-isolation`，免联网）。
- 显式 `packages` 白名单的顶层包内新模块会被包含，但**没有显式写进 `packages` 的新子包**
  （如将来新增 `mavisframework/prompts` 这类子目录若含 `.py` 会被 setuptools 警告忽略）——
  构建日志会打 `Package 'X' is absent from the packages configuration`，看到这类 warning 要补白名单。
- 验证"守卫会红"时切忌 `git checkout` 还原整文件（见 6.2，已写入工作约定）。
- 进真实主线前，务必核对当前没有实时可视化运行在跑（重装 editable 元数据会中断 live_run，
  见 bump 方案方法 §4）。

## 交付确认

预演完成后已删除 `D:\zzr\drill_tmp\mavis_dryrun` 与 `D:\zzr\drill_tmp\ci_venv` 两个临时目录，
真 mavis 与 provenance 之外无残留中间产物。