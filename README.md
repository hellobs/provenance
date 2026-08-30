# Provenance

English | [简体中文](./README_zh.md)

A multi-agent simulation platform built on the self-developed
[mavisframework](https://github.com/hellobs/mavis) engine, demonstrating
"AI value formation is observable and governable" (Global Trust Challenge).
The application scenario is investment advisory (secondary market): agents
make context-based judgments, move and converse within a spatial environment,
with every step configurable, explainable and visualizable in real time.

## 1. Architecture

```
Provenance (platform, this repo)
├── provenance/          # platform core
│   ├── live_fastapi.py  # real-time simulation + visualization (FastAPI + WebSocket, single entry)
│   ├── frontend/        # Phaser frontend + texture pool (agents_pool/)
│   ├── scenarios/       # business scenario configs (investment: roles/relations/story)
│   ├── data/            # configs & prompts
│   └── results/         # checkpoints & decision traces (decisions.json)
└── depends on mavisframework  # engine (separate repo hellobs/mavis, installed as wheel)
```

The platform and the engine are separated: **mavisframework** lives in
[hellobs/mavis](https://github.com/hellobs/mavis); this platform depends on it
via `mavisframework==1.0.0` in `requirements.txt`. The role configuration tool
(config_tool) also belongs to the engine repo.

## 2. Environment & Engine Setup

The platform depends on `mavisframework==1.0.0` (not on PyPI; built from
source). Execute in order:

```bash
# 2.1 Clone the engine repo and build the wheel
# HTTPS (recommended for read-only, no SSH key needed):
git clone https://github.com/hellobs/mavis.git ../mavis
#   or SSH (requires a configured SSH key added to your GitHub account):
# git clone git@github.com:hellobs/mavis.git ../mavis
cd ../mavis
uv build                              # produces dist/mavisframework-1.0.0-py3-none-any.whl
cd ../provenance

# 2.2 Create the environment and install dependencies (uv or conda)
# uv
uv venv .venv --python 3.12
uv pip install ../mavis/dist/mavisframework-1.0.0-py3-none-any.whl
uv pip install -r requirements.txt

# conda
conda create -n provenance python=3.12
conda activate provenance
pip install ../mavis/dist/mavisframework-1.0.0-py3-none-any.whl
pip install -r requirements.txt
```

> Requires [uv](https://docs.astral.sh/uv/) or [conda](https://docs.conda.io/).
>
> **For development/collaboration you may use editable install instead**
> (framework code changes take effect immediately; after framework updates just
> `git pull` — no reinstall needed):
> ```bash
> uv pip install -e ../mavis   # or pip install -e ../mavis
> ```
> Editable and wheel installs are interchangeable (see the Versioning section
> of the engine README).

## 3. Configure the LLM (choose one)

- **Local Ollama** (free, recommended for development): install
  [Ollama](https://ollama.com/) and pull models

  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull qwen3-embedding:0.6b-q8_0
  ```

  No configuration change needed (Ollama is the default).

- **DeepSeek API**: configure `LLM_API_KEY` in `provenance/.env`

  ```
  LLM_API_KEY=your-key
  ```

  and edit `provenance/data/config.json` → `agent.think.llm`:

  ```json
  "llm": {
    "provider": "openai",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": ""
  }
  ```

## 4. Run the Live Simulation

```bash
cd provenance/provenance
python live_fastapi.py --name sim-test --start "20250213-09:30" --stride 2 --step 0 --port 5001
```

Open http://127.0.0.1:5001/ in a browser.

## 5. Role Configuration

Roles, relations and story are configured through web forms (no hand-written
JSON). The tool lives in the engine repo:

```bash
cd ../mavis/config_tool
python app.py
```

Open http://127.0.0.1:5002/

- `/` — role configuration form (generates validated JSON)
- `/relationships` — relation input (appended to relationships.json)
- `/story` — story input (appended to story.json)
- `/agents` — list of configured roles

See `../mavis/config_tool/角色字段清单.md` for the field list. config_tool
writes into this platform's `provenance/frontend/static/assets/village/agents/`
and `provenance/scenarios/` by default (override with `MAVIS_ASSETS_ROOT` /
`MAVIS_SCENARIOS_DIR`). Restart the simulation server (5001) after adding roles.

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
python live_fastapi.py --name stock-en6 --resume --step 0 --port 5001
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
  dead connections (server heartbeat every 5s, 20s staleness timeout,
  focus-return check)
- The live service is driven by mavisframework (Game + Simulator + LiveCompressor)
- **API endpoints**: `GET /api/goals` (constraints/tendency/interventions/
  role_types/embedding_health), `POST /api/goals` (expert constraint edit →
  writes governance.json + interventions.json audit; rejects numeric/zero
  garbage goals), `GET /api/export-chart?agent=<name>` (matplotlib PNG)
- Decision export: `decisions.json` (time/role/action/others/importance) for
  governance platforms and expert UI
- Phaser script: the server prefers the local
  `frontend/static/vendor/phaser.min.js` (works offline) and falls back to CDN.
  For offline use, download
  `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js` (~1.3MB)
  into that folder before first run
- Localization: modify the engine's `mavisframework/prompt/scratch.py` and
  frontend copy; no logic changes required
- **Role/scenario config tool**: agents, relationships and story events are
  generated via the form-based tool `config_tool` (port 5002, lives in the
  [mavis](https://github.com/hellobs/mavis) repo, `config_tool/`); it writes
  directly into this platform's `agents/` and `scenarios/` directories (see
  `config_tool/README.md`).

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

