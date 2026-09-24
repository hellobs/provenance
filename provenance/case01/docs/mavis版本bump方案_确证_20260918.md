# mavis 版本号隐患 · 确证结论与 bump 三步方案

> **状态**:历史存档
> **最后核对**:2026-09-24
> **说明**:同上
> 2026-09-18 · 只读确证，未改任何代码/依赖。bump 实改归人工拍板项（转接文档 §6.1 / §7.2）。

## 1. 隐患已实锤（有代码证据，非推测）

因果链：

1. mavis 三处钩子在**源码**中确实存在：
   - `runtime/simulator.py`：`__init__(..., external_state=None, interaction_request=None)`（第 31–32 行参数声明，62/74 行调用处）
   - `core/agent_core.py`：`role_directive`（第 93 行）、`set_step_context`（第 363 行）、`request_interaction`（第 375 行）
2. `dist/mavisframework-1.0.0.tar.gz`（2026-08-25 构建，77 文件）里**一个钩子都不含**：
   - 解包后 `runtime/simulator.py` 中 `external_state`/`interaction_request`/`set_step_context`/`request_interaction` 计数全为 **0**
   - `core/agent_core.py` 中 `role_directive` 计数为 **0**
3. 真正会挂的是**构造期**：`case01/injector/bridge.py:218-222` 每次按
   `Simulator(external_state=self.external_state, interaction_request=self.interaction_request)`
   构造。旧 dist 的 `Simulator.__init__` **没有这两个形参** →
   `TypeError: __init__() got an unexpected keyword argument 'external_state'`。

   （注：模拟器自身的 `_inject` 对钩子缺失是安全的——`is not None` 判断、missing 则跳过；但 bridge 构造传参在更前面就炸了。所以"容器装错环境"的失败点是 bridge 构造期，TypeError 表述准确。）

4. 本机能跑只因为 `.venv-live` 是 **editable 安装**：
   `site-packages/__editable__.mavisframework-1.0.0.pth` → `import __editable___mavisframework_1_0_0_finder` 指向 `D:\zzr\mavis\mavisframework` 源码目录。
   一旦脱离 editable（按 requirements 从 PyPI/Git 装），就拿不到钩子。
5. `provenance/requirements.txt` 写死 `mavisframework==1.0.0`——而 `1.0.0` 这个版本号现在
   **对应两份内容不同的制品**（旧 tar 无钩子；源码/未来 wheel 有钩子）。

结论：版本号 hardcode + 制品未重建 = 复现环境必挂。这是"同一版本号两份内容"的经典错配。

## 2. bump 三步方案（确切，可执行）

建议 `1.0.0 → 1.1.0`（半功能增量，符合：新增能力但 API 表面不变；三处钩子均为可选参数/新增方法，向后兼容）。

### 步骤 1 —— mavis 侧 bump + 重建制品
- 改 `D:\zzr\mavis\pyproject.toml` 第 7 行 `version = "1.1.0"`
- 确认 `uv.lock` / `*.egg-info` 里对应版本引用同步（如锁定则需 `uv lock` 刷新）
- 于 `D:\zzr\mavis` 构建 sdist + wheel：
  - `python -m build`（需 build 已装）或 `python -m pip wheel . -w dist` + `python -m build --sdist`
- 产出一致性校验（关键，避免再犯）：解包新 sdist，断言 `runtime/simulator.py` 含
  `external_state`、`core/agent_core.py` 含 `role_directive`；grep 数字非 0 才放行。
- 建议保留一个 `dist/mavisframework-1.1.0.tar.gz` 与 wheel 的 hash 记录。

### 步骤 2 —— 本地隔离复现验证
- 在干净解释器（或临时 venv）用**新 wheel/sdist** 安装 `mavisframework==1.1.0`，
  跑 `case01/tests` 最小冒烟（dry-run bridge 构造），确认不再 `TypeError`。
- 同时留证：用旧 `1.0.0` tar 装一遍，复现 `TypeError`，作为"修前会挂"的对照。

### 步骤 3 —— provenance 侧更新 pin
- `provenance/requirements.txt`：`mavisframework==1.0.0` → `mavisframework==1.1.0`
- 若依赖走 Git 而非 PyPI，把引用/tag 指到含新提交的提交（合并顺序见第 3 节）。

## 3. 依赖合并顺序（与转接文档 §7.2 一致）

1. 先把 mavis `feat/generic-injection-hooks` 合入 mavis main（可直接合并，领先 2、落后 0）。
2. 合入后**再** bump 到 1.1.0 并重建制品（保证产物含钩子且版本能区分）。
3. provenance `feat/case01-injector` 先 rebase 到含 `7ae216f` 的 main，再更新 pin 合并。

建议在 bump 时确保当前**没有实时运行在跑**（可插拔可视化 live_run 若在，重装 editable 元数据会中断）。

## 4. 待人工拍板

- 是否现在执行（转接文档 §7.2 的关口）—— 方案已就绪，点头即可落地。

---

## 5. 执行记录（2026-09-18 已落地，由 zzr 执行）

人工点头后按本文第 2/3 节顺序执行完毕：

1. **mavis 合主线**：`feat/generic-injection-hooks`（`64780ac`）快进合并进 `main` 并推送
   （`0bc18de..64780ac`）。合并后 main 测试 **96 passed**。
2. **bump + 重建**：main 上 `1.0.0 → 1.1.0`（提交 `ca0af12`），改动
   `pyproject.toml` / `mavisframework/__init__.py` / `uv.lock` / `README.md` / `README_zh.md`。
   `python -m build --no-isolation` 产出 sdist + wheel：
   - sdist SHA256 `4C19459EC838696374434184FFA1B9569A2C8A968498E5E36DE2519678226452`
   - wheel SHA256 `C2F4889F0B2F97F4E4E5F02297A3EADC6D0818685C1E8603131A701BCB11DBD0`
   - 解包校验：两份产物里 `external_state` / `interaction_request` / `role_directive`
     全部存在；wheel METADATA `Version: 1.1.0`。
3. **隔离对照验证**（新建临时 venv，**在非源码目录里跑**）：
   - 装 1.1.0 wheel → `Simulator(max_workers=2, external_state=..., interaction_request=...)`
     构造成功，`mavisframework.__version__ == "1.1.0"`；
   - `--force-reinstall` 旧 1.0.0 sdist → 精确复现
     `TypeError: Simulator.__init__() got an unexpected keyword argument 'external_state'`。
4. **provenance pin 同步**：`requirements.txt` → `mavisframework==1.1.0`，
   并写明"不要退回 1.0.0"及原因；`README.md` / `README_zh.md` 的版本号与 wheel 文件名同步。

踩坑记录（值得下一棒记住）：第一次验证时 shell 的 cwd 在 `D:\zzr\mavis`，
`import mavisframework` 命中了工作树源码而不是 venv 里的 site-packages，
于是"装旧包也照样通过"——**验证已安装包必须换到无关目录再跑**。