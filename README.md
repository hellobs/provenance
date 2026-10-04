<h1 align="center">Provenance</h1>

> **Status**:current — Provenance platform overview (English); the entry point of this repository

**AI value formation is observable, governable and auditable.**

**Built on [mavisframework](https://github.com/hellobs/mavis) v1.3.3** (a self-developed generative multi-agent simulation engine, versioned independently). The application scenario is investment advisory (secondary market): agents make context-based judgments, move and converse within a spatial environment, with every step configurable, explainable and visualizable in real time.

[![based on mavisframework](https://img.shields.io/badge/based%20on-mavisframework%201.3.3%20%C2%B7%20engine-7c3aed?style=flat-square&labelColor=1f2328)](https://github.com/hellobs/mavis) [![License](https://img.shields.io/badge/license-Apache--2.0-3b82f6?style=flat-square&labelColor=1f2328)](LICENSE) [![Tests](https://img.shields.io/badge/tests-passing-2ea043?style=flat-square&labelColor=1f2328)](tests) [![Python](https://img.shields.io/badge/python-%E2%89%A5%203.12-3776ab?style=flat-square&labelColor=1f2328)](requirements.txt) [![Live](https://img.shields.io/badge/live-127.0.0.1%3A5010-009688?style=flat-square&labelColor=1f2328)](provenance/docs/平台对接契约_5010唯一入口.md) [![Scenarios](https://img.shields.io/badge/cases-3%20declared-f59e0b?style=flat-square&labelColor=1f2328)](provenance/cases)

**English** | [简体中文](./README_zh.md)

---

> **Abstract**
>
> This is a multi-agent simulation platform. The application scenario is investment advisory (secondary market): agents make context-based judgments, move and converse within a spatial environment, with every step configurable, explainable and visualizable in real time. It serves the Global Trust Challenge *process-alignment* story — **AI value formation can be observed, governed and audited**.
>
> Platform and engine are separated: [mavisframework](https://github.com/hellobs/mavis) is maintained and released independently (v1.3.3); this repo depends on it via `mavisframework>=1.2.0,<2.0.0` in `requirements.txt`. **Constraints never enter the prompt** — an expert edit only weights the *consequence feedback*, so the tendency converges only through later experience (lagged convergence = observable evidence of internalization).
>
> → [0\. Current state](#0-current-state) ｜ [7\. IVD Governance Platform](#7-ivd-governance-platform) ｜ [Expert tour](provenance/docs/专家导览_怎么看provenance.md)

**Scope**

| Covered | Not covered |
|---|---|
| Real-time simulation & visualization; 5010 as the single integration entry (live face + data face) IVD governance: constraint edits, tendency curves, intervention audit, three-layer explanation Pluggable expert interventions (`InterventionStrategy` registry) Read-only discovery surfaces: expert tour, platform contract, field reading guide Embed surfaces `/embed/*` (iframe, no CORS) | Turnkey support for any other business scenario (new scenario = a `cases/*/scenario.yaml` + an engine) Hardened security for public exposure (**no authentication**; loopback-only by default) A real market model (consequence feedback is a lightweight embedding-similarity stand-in) Final judgment on research conclusions (the AI only guarantees mechanical correctness) |

**Handing over / integrating? Start with [provenance/docs/文档索引.md](provenance/docs/文档索引.md)** — it shows at a glance which documents are current and which are superseded. The single contract handed to a governance platform is [平台对接契约_5010唯一入口.md](provenance/docs/平台对接契约_5010唯一入口.md).

---

## Table of Contents

* [0\. Current state](#0-current-state)
* [1\. Architecture](#1-architecture)
* [2\. Environment & Engine Setup](#2-environment--engine-setup)
* [3\. Configure the LLM](#3-configure-the-llm)
* [4\. Run the Live Simulation](#4-run-the-live-simulation)
* [5\. Role Configuration](#5-role-configuration)
* [6\. Run Options](#6-run-options)
* [7\. IVD Governance Platform](#7-ivd-governance-platform)
* [8\. Deploy & Embed](#8-deploy--embed)
* [9\. Notes](#9-notes)
* [10\. Custom Maps](#10-custom-maps)
* [11\. References](#11-references)
* [12\. Security & exposure](#12-security--exposure)

---

## 0. Current state

(checked 2026-10-01)

**Layers**: scenario declaration (data) -> engine (`case_engine/`, registrable & replaceable)
-> case (`case01/`, `case00/`) -> kernel (`mavisframework`, independently versioned, v1.3.3)
-> presentation (`packages/mavis-vizkit`, a mavis plugin).

**Interventions are pluggable**: the three expert write endpoints run through an
`InterventionStrategy` registry (`live/interventions.py`; built-in `goals` / `undo`
/ `mark` / `corrective_feedback` — expert corrections injected into the agent's
memory stream). New intervention = one subclass + one `register()` call, exposed
automatically via `POST /api/intervention/{strategy_id}` and listed at
`GET /api/interventions`. Legacy paths (`/api/goals` etc.) remain as thin shells.

**Branch judging is LLM-first**: on 25 recorded T0 answers the keyword rules
table matched the recorded branch 1/25 (long answers always trip a conditional
keyword), while an LLM judge (GLM-4.7-flash, thinking off) matched 15/16
judge-mode records (93.8%). Rules stay as the offline fallback; the evaluation
harness is `case01/tools/branch_judge_eval.py` (report:
`results/analysis/branch_judge_eval/`).

**Services (localhost)**:

- `5010` the single integration entry (live face + data face: aggregate `/api/runs`,
  expert-safe `/api/run-detail/...`, six `/embed/*` surfaces)
- `5002` case01 read-only contract · `5003` case00 archive (read-only) ·
  `5020` staged pixel scene · `8060` config tool
  (`5004` multi-scenario panel retired — review panel debug mode only)

**Tests**: `python -m pytest tests` (outer) · `python -m pytest case01/tests case_engine/tests`
· the `packages/mavis-vizkit` suite · the `../mavis` kernel suite.

**Docs**: start at `provenance/docs/文档索引.md` (status index); the platform-facing contract is
`provenance/docs/平台对接契约_5010唯一入口.md`.

## 1. Architecture

```
Provenance (platform, this repo)
├── provenance/          # platform core
│   ├── live_fastapi.py  # real-time simulation + visualization (FastAPI + WebSocket, single entry)
│   ├── case_engine/     # engine layer: scenario declaration -> registrable/replaceable runtime
│   ├── cases/           # scenario declarations (data): case00_village / case01_stock / case02_minimal
│   ├── case01/          # case: investment advisory (secondary market) baseline
│   ├── case00/          # case: village (frozen; kept for comparison & display)
│   ├── live/            # live face: intervention strategies, network guard, ...
│   ├── frontend/        # Phaser frontend + texture pool (agents_pool/)
│   ├── scenarios/       # legacy business scenario configs (investment: roles/relations/story)
│   ├── data/            # configs & prompts
│   └── results/         # checkpoints & decision traces (decisions.json)
├── packages/            # local packages: mavis-vizkit (presentation) / mavis-case01-injector
└── depends on mavisframework  # engine (separate repo hellobs/mavis, installed as wheel)
```

The platform and the engine are separated: **mavisframework** lives in
[hellobs/mavis](https://github.com/hellobs/mavis); this platform depends on it
via `mavisframework>=1.2.0,<2.0.0` in `requirements.txt`. The role configuration tool
(config_tool) also belongs to the engine repo.

## 2. Environment & Engine Setup

The platform depends on `mavisframework>=1.2.0,<2.0.0` (not on PyPI; built from
source). Do not go below 1.2.0: 1.0.0 predates the three case01 injection hooks
(`external_state` / `interaction_request` / `role_directive`) and constructing
`Simulator` with them raises `TypeError`, while 1.1.0 predates the generic plugin
surface (`mavisframework.plugin`, `Simulator(plugins=)`,
`agent_core.subscribe_chat_line`). CI asserts that surface is present, via
`tools/check_engine_baseline.py`, before running any test.
Execute in order:

```bash
# 2.1 Clone the engine repo and build the wheel
# HTTPS (recommended for read-only, no SSH key needed):
git clone https://github.com/hellobs/mavis.git ../mavis
#   or SSH (requires a configured SSH key added to your GitHub account):
# git clone git@github.com:hellobs/mavis.git ../mavis
cd ../mavis
uv build                              # produces dist/mavisframework-1.3.3-py3-none-any.whl
cd ../provenance

# 2.2 Create the environment (uv or conda; Python 3.12)
uv venv .venv --python 3.12
#   conda users: conda create -n provenance python=3.12 && conda activate provenance

# 2.3 Install dependencies **in this exact order** (all three steps matter)
#     a) the framework (wheel built in 2.1; `pip install -e ../mavis` also works)
uv pip install ../mavis/dist/mavisframework-1.3.3-py3-none-any.whl
#     b) the two **local packages** in this repo (not on PyPI; skipping this makes
#        the next step fail with "No matching distribution found")
uv pip install -e packages/mavis-vizkit -e packages/mavis-case01-injector
#     c) the rest of the runtime deps + the test runner (requirements.txt has NO pytest)
uv pip install -r requirements.txt pytest
```

> Requires [uv](https://docs.astral.sh/uv/) or [conda](https://docs.conda.io/); `pip` works the same.
>
> **Self-check** (`pytest.ini` sets `pythonpath=provenance`, so running from the repo
> root or from `provenance/` both work):
> ```bash
> pytest tests                                            # outer: API / pages / guards
> pytest provenance/case_engine/tests provenance/case01/tests
> ```

## 3. Configure the LLM (choose one)

- **Local Ollama** (free, recommended for development): install
  [Ollama](https://ollama.com/) and pull models

  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull qwen3-embedding:0.6b-q8_0
  ```

  No configuration change needed (Ollama is the default).

- **OpenRouter (or any OpenAI-compatible API), needs a key**: one command writes
  the key and **verifies it online** (the key is never printed):

  ```bash
  python provenance/tools/setup_api.py --key sk-xxxx
  python provenance/tools/setup_api.py --show      # just show where the key comes from
  ```

  Or copy `.env.example` (repo root) to `.env` and fill in `OPENROUTER_API_KEY=...` —
  `.env` is **loaded automatically** (it never overrides real env vars), so you do not
  need to know how to set environment variables. Both `.env` and `.secrets.json` are
  gitignored. Resolution order: `env var → .env → .secrets.json` (repo root / package root).

## 4. Run the Live Simulation

```bash
cd provenance/provenance
python live_switch.py --start case01        # 5010 is the single live entry (case00/case01 are mutually exclusive)
# UI only, no simulation: python live_switch.py --start case00 --no-sim
```

Open http://127.0.0.1:5010/ . Read-only faces: `python -m case00.serve --port 5003` /
`python -m case01.serve --port 5002`; the config tool lives in this repository:
`cd provenance/config_tool && python app.py` (8060). It discovers the co-located
platform directory; no environment variables are needed in the standard layout.

**Experts/reviewers** should read `provenance/docs/专家导览_怎么看provenance.md` first:
conclusion boundaries, which faces exist only on the case01/case00 side, the two read-only
data endpoints, how to read the fields, how to use the two manual-annotation tables, and a
fifteen-minute walkthrough.

## 5. Role Configuration

Roles, relations and story are configured through web forms (no hand-written
JSON). The tool lives in this repository:

```bash
cd provenance/config_tool
python app.py
```

Open http://127.0.0.1:8060/

- `/` — role configuration form (generates validated JSON)
- `/relationships` — relation input (appended to relationships.json)
- `/story` — story input (appended to story.json)
- `/agents` — list of configured roles

See `provenance/config_tool/角色字段清单.md` for the field list. config_tool
writes into this platform's `provenance/frontend/static/assets/village/agents/`
and `provenance/scenarios/` by default (override with `MAVIS_ASSETS_ROOT` /
`MAVIS_SCENARIOS_DIR`). Restart the simulation server (5010) after adding roles.

## 6. Run Options

| Option | Description |
|---|---|
| `--name` | simulation name (unique; checkpoints stored per name) |
| `--start` | starting time |
| `--stride` | game minutes per step (2 for finer detail) |
| `--step` | step count, `0` = run forever |
| `--resume` | resume from a checkpoint |
| `--port` | server port |

## 7. IVD Governance Platform

This platform is the reference implementation of IVD's *process alignment*
story: **AI value formation can be observed, governed and audited**.

### 7.1 Institutional layer (governance.json)

Expert-set *constraints/expectations* live in
`provenance/governance.json` (NOT in agent bodies). Each role maps to a
`{goal: weight}` vector summing to 1, where goal names are *behavior-bound*
(designed so embedding feedback can distinguish them — e.g. "Risk Control"
for stress-testing, "Data Rigor" for cross-verification):

```json
{ "roles": { "AI投顾助手": { "Serve Users": 0.35, "Compliance Rigor": 0.3, "Risk Control": 0.2, "Data Rigor": 0.15 } } }
```

Each role's `agent.json` also carries `initial_tendency` (persona baseline,
slightly offset from the constraints). On `--resume`, `value_tendency` and the
experience count are restored from the checkpoint so the tendency curve stays
continuous across restarts.

Constraints never enter the prompt; they only weight the consequence
feedback, so an expert adjustment is *felt* by the agent through later
experience (lagged convergence = internalization evidence).

### 7.2 Governance panel (live adjust)

The browser panel (right side) lets an expert:

- **Read** each role's value tendency (internalized result, read-only) as a
  live curve — one line per constrained goal, plus a *stepped dashed line*
  for the constraint expectation (steps at each expert intervention) and a
  vertical marker at each intervention time;
- **Adjust** constraint weights with sliders (sum enforced to 1; submitted on
  slider release, not per drag tick — avoids flooding the audit log);
- **Export** the tendency chart as PNG via the backend
  (`GET /api/export-chart?agent=...`, matplotlib-rendered, stepped constraint
  lines, compact bottom legend).

### 7.3 Audit trail

- `interventions.json` — every expert edit:
  `{time, sim_time, agent, old_constraints, new_constraints, operator}`;
- `decisions.json` — per-step decision stream with `goal_alignment` (instant)
  and `value_tendency` (accumulated) for each role;
- the tendency curve itself: lag between an intervention and the tendency's
  convergence is the observable evidence of internalization.

### 7.4 Mechanism summary

`action → embedding similarity vs behavior-bound goals → relative share ×
weight → sliding window → tendency (blend with persona baseline) → prompt →
action`. Goal names are designed to be *semantically distinguishable* so the
embedding feedback can tell actions apart (see §7.1); scenario events and
role daily plans rotate behaviors to keep the curves lively instead of flat.
See the engine's README §7 for the formalization.

### 7.5 Explainability panel (`/api/explain`)

`GET /api/explain?agent=<name>` returns three explanation layers for why a
role's value tendency is what it is:

1. **Decomposition** — `tendency = α×persona baseline + (1−α)×experience
   window mean`, per goal, with α and cumulative experience count;
2. **Window details** — recent experiences (action description, per-goal
   alignment, feedback) that drove the internalization;
3. **Intervention chain** — each expert intervention with constraint jump,
   tendency before/after 2h, and the quantified shift (lagged internalization
   evidence).

The browser panel shows these via the *"解释倾向成因"* button per role.

## 8. Deploy & Embed

### 8.1 Runtime requirements

| Component | Notes |
|---|---|
| Python 3.12 + venv | `pip install -r requirements.txt` + build/install mavis wheel |
| LLM | Local Ollama (qwen3-instruct + qwen3-embedding) **or** OpenAI-compatible API (set in `data/config.json`, see §3) |
| Frontend assets | Vendored locally (`static/vendor/`: phaser/jquery/bootstrap) — no CDN dependency |

### 8.2 Running a server

```bash
# from provenance/provenance
python live_fastapi.py --name stock-en6 --resume --step 0 --port 5010
# fresh sim (no --resume) starts at the configured date; --step 0 = run forever
```

Behind a reverse proxy (nginx/caddy) for HTTPS when embedding into an
external platform. The service is self-contained (FastAPI + WS + static);
no build step needed.

### 8.3 Embedding into another platform (iframe)

The service exposes dedicated *embed routes* — slim pages that reuse the same
WebSocket/data but hide unrelated UI. Embed via `<iframe>` from any web
platform (e.g. a governance dashboard); iframe pages connect their own WS, so
no CORS setup is required.

| Route | Content |
|---|---|
| `/embed/scene` | Phaser canvas only (no floating panels) — for a "simulation" slot |
| `/embed/goals` | Governance panel only (sliders + tendency curve + explain button) |
| `/embed/explain` | Governance panel with the explanation panel auto-expanded |
| `/embed/timeline` | Intervention timeline panel (all agents, full page) — for an "audit trail" slot |

Example (React/Next.js):

```jsx
<iframe src="https://sim.example.com/embed/scene" style={{width:'100%',height:'480px',border:0}} />
<iframe src="https://sim.example.com/embed/goals" style={{width:'380px',height:'70vh',border:0}} />
```

Deployment topology: run provenance on its own domain; the host platform
embeds it. This keeps the two codebases independent while sharing the same
live simulation.

## 9. Notes

- Real-time visualization via WebSocket (`/ws`) pushing engine contract
  messages (agent/time/chat_line/snapshot); the client watchdog reloads on
  dead connections. Server sends an independent heartbeat every 5s
  (asyncio task, not queue-driven — reliable even during long LLM-only
  gaps such as schedule building); client 20s staleness timeout +
  focus-return check
- The live service is driven by mavisframework (Game + Simulator + LiveCompressor)
- **API endpoints**:
  - `GET /api/goals` — constraints/tendency/interventions (scoped to the
    current simulation via `simulation` field)/role_types/embedding_health
  - `POST /api/goals` — expert constraint edit → writes governance.json +
    interventions.json audit (with `simulation` tag + optional `note` reason);
    rejects numeric/zero garbage goals; sum must equal 1
  - `POST /api/undo-intervention` — roll back a past intervention to its
    `old_constraints` (matched by agent + sim_time + record time); appends an
    `operator=undo` audit record and marks the original record `revoked` —
    history is never deleted, only amended
  - `GET /api/timeline` — intervention timeline across all agents (sorted by
    sim_time): each event carries old→new constraints, note, operator, and
    tendency shift (same windowing as `/api/explain`); revoked/undo events
    flagged for the frontend
  - `GET /api/export-chart?agent=<name>` — matplotlib PNG of tendency curve
  - `GET /api/explain?agent=<name>` — explainability panel: tendency
    decomposition (α blend), experience window details (action/alignment/
    feedback), intervention causal chain (tendency shift after each
    intervention). Checkpoint series loading is cached by directory mtime.
- Decision export: `decisions.json` (time/role/action/others/importance) for
  governance platforms and expert UI
- **Tests**: `tests/test_live_api.py` (pytest, fake server injection, no
  real simulation/LLM needed) covers goals read/write, intervention scoping,
  explain three layers, export error handling. Engine tests live in the
  mavis repo (`tests/`).
- Phaser script: the server prefers the local
  `frontend/static/vendor/phaser.min.js` (works offline) and falls back to CDN.
  For offline use, download
  `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js` (~1.3MB)
  into that folder before first run
- Localization: modify the engine's `mavisframework/prompt/scratch.py` and
  frontend copy; no logic changes required
- **Role/scenario config tool**: agents, relationships and story events are
  generated by the in-repository `provenance/config_tool/` service (port **8060**);
  it writes directly into this platform's `agents/`, `scenarios/`, and `cases/` directories.

## 10. Custom Maps

1. Follow the maze.py logic in the original generative_agents project to
   support tiled-exported json/csv files
2. Follow the existing maze.json format to merge tiled exports
   (maze_meta_info.json, collision_maze.csv, sector_maze.csv) into a new maze.json
3. **Recommended**: use the bundled converter `tools/tilemap_to_maze.py`
   (CLI, no external deps) — converts a Tiled `.tmx`/`.json` map into
   `maze.json` directly (see `tools/tilemap_to_maze_README.md`). The legacy
   GUI tool is at https://github.com/jiejieje/tiled_to_maze.json

## 11. References

- Paper: [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- Code: [mavisframework (self-developed engine)](https://github.com/hellobs/mavis) / [Generative Agents (original)](https://github.com/joonspk-research/generative_agents) / [wounderland](https://github.com/Archermmt/wounderland)
- Map tool: `tools/tilemap_to_maze.py` (bundled) / [tiled_to_maze (legacy GUI)](https://github.com/jiejieje/tiled_to_maze.json)

## 12. Security & exposure

(2026-09-23)

**None of these services has authentication.** Anyone who can reach a port can read every
run record; 5010 additionally lets them rewrite governance weights, undo interventions,
mark reflections and restart a run (4 write endpoints in total); 8060 (config tool) can
edit scenarios, delete roles and launch a run.

- **Loopback-only by default.** Binding a non-loopback address requires an explicit
  `LIVE_ALLOW_REMOTE=1`; otherwise the process **refuses to start** and prints exactly what
  would be exposed (a one-line warning is not enough — the port would already be open).
- **CORS defaults to loopback origins only** (when `EMBED_ALLOW_ORIGINS` is unset). For
  cross-origin data access from the platform, set `EMBED_ALLOW_ORIGINS=https://<platform-host>`.
  Plain `<iframe>` embedding does not use CORS and is unaffected.
- **Prefer not exposing ports at all** for cross-machine integration: same-host deployment,
  a read-only reverse proxy (only `/embed/*` and `GET /api/*`), or an SSH tunnel to 5010.
  If you must expose it, use the origin allowlist plus a firewall rule per source IP — and
  **never expose 8060**.
- Secrets: the OpenRouter key lives in `case01/.secrets.json` (gitignored, not in git); a scan
  of 5000+ artifacts and logs found no key material.

Code locations: `live/netguard.py` (binding & allowlist policy), `tests/test_netguard.py`
(behaviour assertions). Integration details: `provenance/docs/给平台侧_嵌入与数据接入.md`;
health-check conclusions: `provenance/docs/0923_体检报告.md`.

## License

Apache License 2.0, see [LICENSE](LICENSE).
