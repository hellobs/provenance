# 任务：mavis 插件面合入 main + bump 1.2.0 + 打 tag + provenance pin 与 CI 基线断言同步

> **状态**:历史存档
> **最后核对**:2026-09-21
> **说明**:任务派发单:任务已完成,留作过程记录
> **最后核对**:2026-09-21
> **说明**:任务派发单:任务已完成,留作过程记录

> 2026-09-18 · 依据已拍板材料执行：`mavis_插件面合并决策_20260918.md`（动作与版本结论）、
> `mavis合并bump预演_20260918.md`（链路已在临时克隆走通，含产物哈希与断言方法）。
> 本任务**执行**这两份材料里的动作，不是再出预演。

## 0. 目标与边界（先把范围钉死）

做四件事，一次做完、一起交证据：

1. mavis：把 `feat/plugin-surface` 快进合并进 `main`。
2. mavis：在 `main` 上把版本 `1.1.0 → 1.2.0`，重建 sdist + wheel，解包校验，打 `v1.2.0`。
3. mavis：推送 `main` 与 tag 到 `origin`。
4. provenance：pin/文档/dev-extra 同步到 1.2.0，并新增"基线上存在插件面"的 CI 前置断言。

**本任务不允许做的事**（越界即失败）：

- 不允许把 provenance 合进它的 `main`。那是下一个关口的独立动作，本轮只改
  `feat/case01-injector` 分支并推送。CI 只在 `push: main` 与 `pull_request` 触发，
  所以本轮推分支不会触发 CI，这是预期的。
- 不允许删除 mavis 的 `feat/plugin-surface` 与 `feat/generic-injection-hooks` 分支（决策材料 §4 共存回滚需要）。
- 不允许改 mavis 的任何既有行为语义，不允许给 mavis 加任何业务词汇（纯洁度）。
- 不允许改 case01 的代码语义。本轮 provenance 侧只动版本引用、CI 配置、新增一个断言脚本。
- 不允许编辑 GTC 侧的 `.docx`（那边无版本管理）。
- 不允许为了"让断言通过"而放宽断言本身。

## 1. 开工前必须取的现状（作为前后对照的"前"）

```
cd D:\zzr\mavis
git status --porcelain            # 必须为空
git log --oneline -1              # 期望 dca75b6
git branch -vv                    # feat/plugin-surface 领先 main 2、落后 0
git tag                           # 期望只有 v1.0-goals-internal
git ls-remote --heads origin      # 期望 main=8f12919, feat/generic-injection-hooks=64780ac
Get-ChildItem dist | Select-Object Name,Length,LastWriteTime   # 期望只有 1.0.0 / 1.1.0 两套旧制品
git rev-list --left-right --count origin/main...feat/plugin-surface   # 期望 "0	2"
git merge-base --is-ancestor origin/main feat/plugin-surface; echo $LASTEXITCODE   # 期望 0(可快进)
```

另外确认**当前没有 live_run / Provenance 服务在跑**（第 4.4 步要刷新可编辑元数据）。

## 2. mavis 侧执行

### 2.1 快进合并

```
cd D:\zzr\mavis
git checkout main
git merge --ff-only feat/plugin-surface
```

验收锚点（预演实测值）：`Updating 8f12919..dca75b6`、`Fast-forward`、
`8 files changed, 748 insertions(+), 32 deletions(-)`，且新增
`mavisframework/plugin.py` 与 `tests/test_plugin_surface.py`。
若不是快进、或改动文件数与插入删除行数不符，停下来报告，不要自行改用普通 merge。

### 2.2 bump 到 1.2.0

按精确字符串改，共 5 个文件 7 处（**只改这些**）：

- `pyproject.toml`：`version = "1.1.0"` → `version = "1.2.0"`
- `mavisframework/__init__.py`：`__version__ = "1.1.0"` → `__version__ = "1.2.0"`
- `uv.lock`：在 `name = "mavisframework"` 那一段里，`version = "1.1.0"` → `version = "1.2.0"`（该段 `source = { editable = "." }`，只此一处）
- `README.md`：构建示例里的 wheel 文件名两处
  `dist/mavisframework-1.1.0-py3-none-any.whl` → `dist/mavisframework-1.2.0-py3-none-any.whl`
- `README_zh.md`：同上，两处

**不要改** `README.md` 与 `README_zh.md` 第 12 节"语义化版本"表里的
`| New feature (backward-compatible) | 1.1.0 |`。它是"新增向后兼容功能就用 1.x 次版本"这条规则的示例，
不是当前版本引用；把它改成 1.2.0 会让示例失去示范意义。
（预演文档把这里记成"待改项"是记错了，以本条为准。请在报告里就此说明你的处理。）

自查：`git grep -n "1\.1\.0"` 的剩余命中应只为上面那张示例表的两处。

提交（commit message 用文件避免引号问题）：

```
chore(release): 版本 1.1.0→1.2.0(插件面入主线,重建制品)
```

### 2.3 重建 sdist + wheel

`.venv-live` 未装 `build` 模块，用已验证过的等价写法（等价 `python -m build --no-isolation`，免联网）：

```
cd D:\zzr\mavis
D:\zzr\provenance\provenance\.venv-live\Scripts\python.exe -c "import setuptools.build_meta as bm; print(bm.build_sdist('dist')); print(bm.build_wheel('dist'))"
```

期望产物：`dist/mavisframework-1.2.0.tar.gz`、`dist/mavisframework-1.2.0-py3-none-any.whl`。
记录两份产物的字节数与 SHA256（预演值可作对照：sdist 120381 字节、wheel 112844 字节，
`plugin.py` 在两份产物里均为 8183 字节；哈希不必与预演相同，字节数应接近）。

构建会生成 `build/`、`*.egg-info/`，二者均在 `.gitignore` 内，
**不要 force-add**。构建后 `git status --porcelain` 必须仍为空（`dist/` 也不入库，这是既有约定，不改）。

### 2.4 产物解包断言（四条，缺一不可）

```
cd D:\zzr\mavis
tar -xzf dist/mavisframework-1.2.0.tar.gz -C dist
D:\zzr\provenance\provenance\.venv-live\Scripts\python.exe -c "import zipfile; zipfile.ZipFile(r'dist/mavisframework-1.2.0-py3-none-any.whl').extractall(r'dist/whl_x')"
```

对 sdist 解出的 `dist/mavisframework-1.2.0/` 与 wheel 解出的 `dist/whl_x/` 两份都断言：

1. `mavisframework/plugin.py` 存在（记录字节数）。
2. `mavisframework/runtime/simulator.py` 含 `external_state` 与 `interaction_request`（计数非 0）。
3. `mavisframework/core/agent_core.py` 含 `role_directive` 与 `subscribe_chat_line`（计数非 0）。
4. sdist 的 `PKG-INFO`、wheel 的 `METADATA` 里 `Version: 1.2.0`。

第 1 条是本轮最关键点：`pyproject.toml` 用显式 `packages` 白名单，新增的顶层包内模块是否真进制品
此前只靠推理，现在必须解包确认。构建日志里应能看到 `adding 'mavisframework/plugin.py'`。

校验完把 `dist/mavisframework-1.2.0/` 与 `dist/whl_x/` 两个解包目录删掉。

### 2.5 mavis 全量测试

```
cd D:\zzr\mavis
D:\zzr\provenance\provenance\.venv-live\Scripts\python.exe -m pytest -o addopts="" -q
```

期望 `122 passed`。注意 pyproject 自带 `-q`，叠加会变成 `-qq`，所以必须用 `-o addopts=""`。

### 2.6 打 tag 并推送

`v1.2.0` 打在 bump 提交上。同时补一个 `v1.1.0`（打在 `8f12919`，即版本仍为 1.1.0 的最后一个提交）：
决策材料 §4 写的回滚方式是"重置到前一 tag（回 v1.1.0 基线）"，而该 tag 目前并不存在，属历史遗漏。
这一步是补锚点，不是新建版本。

```
cd D:\zzr\mavis
git tag v1.2.0
git tag v1.1.0 8f12919
git push origin main
git push origin v1.2.0 v1.1.0
```

推送后取证：`git ls-remote --heads origin`（main 应等于本地 main 的 hash）、
`git ls-remote --tags origin`（应含 v1.2.0 与 v1.1.0）、`git log --oneline -3`。

`feat/plugin-surface` 与 `feat/generic-injection-hooks` 都保留，不删不推（本轮不需要）。

## 3. provenance 侧执行（分支 `feat/case01-injector`，当前 HEAD 8eb1a0b，与 origin 0/0）

### 3.1 版本引用同步

`provenance/requirements.txt`：

- 第 3 至 7 行的注释：把"1.1.0 起含三处注入钩子""mavis main 的 1.1.0 提交为 ca0af12"更新为
  1.2.0 口径（写清 1.2.0 起额外含通用插件面 `mavisframework.plugin`，并写上新 bump 提交 hash 与 `v1.2.0` tag）。
- 第 8 行的 pin：`mavisframework==1.1.0` 改成 **`mavisframework>=1.2.0,<2.0.0`**。

改 pin 形式是我（架构侧）的决定，理由是：CI 是 `git clone --depth 1 ... main` 装框架，main 是移动目标，
写死 `==` 会在 mavis 每次 bump 时立刻把 CI 打红，而那种红并不代表任何真实缺陷；改成"下界 1.2.0 + 上界 2.0.0"
既保证"基线至少是含插件面的版本"（容器里若只有 1.1.0，pip 解析失败、CI 立刻红，且报错直指包名），
又不会在未来的次版本 bump 上误伤。加上第 4 节的基线断言，两道独立守卫各自覆盖"版本"与"能力"。
如果你想否决这个形式，只回一句即可，改回 `==1.2.0` 是一行的事。

其余 live 引用同步到同样的口径与新 wheel 文件名：

- `README.md`（仓库根）第 27、32、45、51、57 行附近提到 `mavisframework==1.1.0` 与
  `mavisframework-1.1.0-py3-none-any.whl` 的地方。
- `README_zh.md`（仓库根）第 24、29、39、45、51 行附近同上。
- `packages/mavis-vizkit/pyproject.toml` 第 14 行 dev extra 里的 `mavisframework==1.1.0`，改成与主 pin 相同的范围。

**只改"活引用"（pin、用法示例、dev extra）。** `provenance/case01/docs/` 下那些写 1.1.0 的历史记录
（执行计划、验收报告、bump 方案执行记录、决策材料、预演文档）一律不改，它们描述的是当时发生的事。
自查：`git grep -n "mavisframework==1\.1\.0"` 应无命中。

### 3.2 新增 CI 基线断言脚本

新建 `tools/check_engine_baseline.py`（仓库根 `tools/`，与 `tilemap_to_maze.py` 同级）：

```python
"""断言已安装的 mavisframework 具备 case01 需要的通用插件面。

CI 在装完框架后先跑本脚本:若容器错装到不含插件面的旧制品(<=1.1.0),
这里在跑测试之前就红,而不是让特性探测静默回退、测试"照样通过"。
"""
import inspect
import sys

import mavisframework
import mavisframework.plugin as plugin_mod
from mavisframework.core import agent_core
from mavisframework.runtime.simulator import Simulator


def main() -> int:
    problems = []
    if not hasattr(plugin_mod, "Plugin"):
        problems.append("mavisframework.plugin.Plugin 缺失")
    if not hasattr(plugin_mod, "PluginManager"):
        problems.append("mavisframework.plugin.PluginManager 缺失")
    if not hasattr(agent_core, "subscribe_chat_line"):
        problems.append("mavisframework.core.agent_core.subscribe_chat_line 缺失")
    if "plugins" not in inspect.signature(Simulator.__init__).parameters:
        problems.append("Simulator.__init__ 无 plugins 参数")

    print(f"mavisframework {mavisframework.__version__} @ {mavisframework.__file__}")
    print("Simulator.__init__ 参数: " + ", ".join(inspect.signature(Simulator.__init__).parameters))
    if problems:
        print("插件面基线断言失败:")
        for item in problems:
            print("  - " + item)
        print("框架必须从 hellobs/mavis main(>=1.2.0)以源码安装;旧制品不含插件面。")
        return 1
    print("插件面基线断言通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

打印 `__file__` 是刻意的：它让"框架到底从 site-packages 还是从工作树源码装的"一眼可查，
这正是上一轮踩过的坑（在源码目录里跑验证会命中工作树，装旧包也照样通过）。

### 3.3 CI 增加前置断言步骤

`.github/workflows/ci.yml` 在"Install dependencies (single source...)"之后、
"Backend API + page render tests"之前插入：

```yaml
      - name: Assert engine baseline exposes plugin surface
        run: |
          python tools/check_engine_baseline.py
```

位置是硬的：必须在依赖装好之后（不然 import 不到框架），在测试套件之前（决策材料 §3 要求"先红"）。

### 3.4 刷新 `.venv-live` 的可编辑元数据（本地环境，不入库）

`.venv-live` 的 `mavisframework` 可编辑元数据仍停在 `1.0.0`
（`site-packages/mavisframework-1.0.0.dist-info`、`__editable__.mavisframework-1.0.0.pth`），
导入拿到的是 1.2.0 源码、但 `pip show` 与 `importlib.metadata` 会报 1.0.0，
且在该 venv 里跑 `pip install -r provenance/requirements.txt` 会因下界 1.2.0 解析失败。

在**确认没有 live_run / 服务在跑**之后刷新（免联网写法）：

```
D:\zzr\provenance\provenance\.venv-live\Scripts\python.exe -m pip install -e D:\zzr\mavis --no-build-isolation --no-deps
```

刷完取证：`.venv-live\Scripts\python.exe -m pip show mavisframework` 的 `Version` 应为 1.2.0；
site-packages 下的 dist-info 目录名应变成 `mavisframework-1.2.0.dist-info`。

## 4. 验收（必须交的证据，按此顺序贴命令与输出）

### 4.1 CI 等价环境复现（硬验收）

新建 py3.12 临时 venv，**严格按改后 CI 的顺序**从零装（不要用 `.venv-live`，它带未声明杂包）：

1. 克隆 mavis main 到临时目录（推完主线的 main）→ `pip install <克隆目录>`（非 editable）
2. `pip install -e packages/mavis-vizkit`
3. `pip install -r provenance/requirements.txt pytest`
4. `python tools/check_engine_baseline.py`
5. `python -m pytest tests -v`（仓库根）
6. `node tests/frontend_smoke.js tests/_pages`
7. `cd provenance` → `python -m pytest case01/tests -q`

期望：第 4 步打印 1.2.0 与通过；第 5 步 23 passed；第 6 步两个页面 PASS；
第 7 步 **207 passed**。全部贴汇总结论行。

### 4.2 断言确实会红（反证，必做）

在另一个临时 venv 里装旧制品 `D:\zzr\mavis\dist\mavisframework-1.1.0-py3-none-any.whl`（非 editable），
再跑 `python tools/check_engine_baseline.py`，期望**非零退出**且打印缺失项清单。
这证明这道守卫不是摆设。全程只动临时 venv，不碰仓库文件。

### 4.3 mavis 侧证据

- 前后对照：HEAD / main / origin/main / tag 列表 / `dist/` 内容（旧制品的时间戳与字节数不得变化）。
- 合并输出原文（含 `Fast-forward` 与 8 files / 748 insertions / 32 deletions）。
- `git grep -n "1\.1\.0"` 的剩余命中（只应是 README 语义化版本示例的两处）。
- 构建输出（两份产物路径、字节数、SHA256）、`plugin.py` 在两份产物里的字节数、
  PKG-INFO 与 METADATA 的 `Version: 1.2.0`。
- `122 passed` 的汇总行。
- `git ls-remote --heads origin` 与 `git ls-remote --tags origin`，以及 `git status --porcelain` 为空。

### 4.4 provenance 侧证据

- `git status --porcelain` 为空、`git log --oneline -3`、`git rev-list --left-right --count origin/main...feat/case01-injector`。
- `git grep -n "mavisframework==1\.1\.0"` 无命中；`requirements.txt` 与 `packages/mavis-vizkit/pyproject.toml` 的新 pin 原文。
- `ci.yml` 的完整 diff。
- 3.4 步的 `pip show` 输出与 site-packages 目录名。

### 4.5 报告与留痕

- 在聊天里给完成报告（中文、不要表格、不要比喻、结论简短）。
- 把执行记录追加进 `provenance/case01/docs/mavis_插件面合并决策_20260918.md`，新开一节"执行记录"，
  写清：合并提交 hash、bump 提交 hash、两个 tag、两份产物字节数与 SHA256、四套测试数、
  以及本任务改了哪些 provenance 文件。格式参照 `mavis版本bump方案_确证_20260918.md` 第 5 节的写法。
- 提交并推送：`git add` 上述改动（不要带 `results/`、临时目录、`dist/` 解包残留），
  commit message 自拟但需点明"mavis 合主线与 bump 1.2.0 + pin/CI 断言同步"，push 到 `origin/feat/case01-injector`。
- 删掉全部临时 venv 与临时克隆目录，并在报告里确认已删。

## 5. 已知坑（本轮必须避开）

- **不要在包源码目录里跑安装验证**：`import mavisframework` 会命中工作树源码而不是 venv 的
  site-packages，于是"装旧包也照样通过"。验证已安装包必须 `cd` 到无关目录（上一轮踩过）。
- **不要用 `git checkout` 还原整文件**去验证"守卫会红"，那会丢掉同轮未提交的改动
  （上一轮已因此丢过一次 YAML 改写）。要验红就把改动"删一段 → 加回来"，或做纯内存比较。
- **`build` 模块在 `.venv-live` 未装**，用 `setuptools.build_meta` 直接调 `build_sdist`/`build_wheel`，
  不要为了 `python -m build` 去联网装 `build`。
- **显式 `packages` 白名单**：构建日志若出现 `Package 'X' is absent from the packages configuration`
  这类 warning，说明有新子包漏进白名单，要停下来报告，不要忽略。
- **CI 触发条件**：`push: main` 与 `pull_request`。本轮推分支不会跑 CI，所以 4.1 的本地
  CI 等价复现是唯一的合前证据；别把"推上去了"当成"CI 绿了"。
- **pin 必须与 mavis 主线推送同轮落地**：mavis main 一旦是 1.2.0，provenance 任何后续的
  `push: main` 或 PR 都会 clone 到 1.2.0；此时若 pin 还是 1.1.0 就会失败。所以第 2 节做完立刻做第 3 节。
- **`.venv-live` 元数据陈旧**：见 3.4，别让 `pip show` 继续报 1.0.0。
- **不要为了让断言过而放宽断言**。断言红了说明基线不对，要修基线，不是修断言。

## 6. 完成判据（我这边复核时会逐条验）

1. `origin/main` 的 mavis 与本地 `main` 一致，且 `v1.2.0`（以及补的 `v1.1.0`）已推上 origin。
2. 7 处 live 版本引用全为 1.2.0；README 的 semver 示例表未被误改。
3. 两份 1.2.0 产物都含 `mavisframework/plugin.py`，版本元数据为 1.2.0。
4. mavis 全量 122 passed。
5. provenance 无残留 `mavisframework==1.1.0`；pin 为新范围；vizkit dev extra 同步。
6. `tools/check_engine_baseline.py` 在 CI 等价环境通过，且在 1.1.0 制品上确实非零退出。
7. CI 等价复现：case01 207 passed、外层 23 passed、前端冒烟两页 PASS。
8. 两仓工作树干净，provenance 分支已推送且与 origin 0/0。
9. 临时 venv 与临时克隆已删除并确认。
