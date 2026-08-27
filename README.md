# Provenance

English | [简体中文](./README_zh.md)

A multi-agent simulation platform built on the self-developed
[mavisframework](https://github.com/hellobs/mavis) engine, demonstrating
"AI value formation is observable and governable" (Global Trust Challenge).
The application scenario is investment advisory (secondary market): agents
make decisions, move and converse autonomously within a spatial environment,
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
git clone git@github.com:hellobs/mavis.git ../mavis
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
`{goal: weight}` vector summing to 1:

```json
{ "roles": { "AI投顾助手": { "Serve Users": 0.5, "Compliance Rigor": 0.5 } } }
```

Constraints never enter the prompt; they only weight the consequence
feedback, so an expert adjustment is *felt* by the agent through later
experience (lagged convergence = internalization evidence).

### 7.2 Governance panel (live adjust)

The browser panel (right side) lets an expert:

- **Read** each role's value tendency (internalized result, read-only) as a
  live curve — one line per constrained goal, plus a dashed line for the
  constraint expectation and a vertical marker at each expert intervention;
- **Adjust** constraint weights with sliders (sum enforced to 1);
- **Export** the tendency chart as PNG.

### 7.3 Audit trail

- `interventions.json` — every expert edit:
  `{time, sim_time, agent, old_constraints, new_constraints, operator}`;
- `decisions.json` — per-step decision stream with `goal_alignment` (instant)
  and `value_tendency` (accumulated) for each role;
- the tendency curve itself: lag between an intervention and the tendency's
  convergence is the observable evidence of internalization.

### 7.4 Mechanism summary

`action → embedding similarity vs constrained goals → relative share × weight
→ sliding window → tendency (blend with persona baseline) → prompt → action`.
See the engine's README §7 for the formalization.

## 8. Notes

- Real-time visualization via WebSocket (`/ws`) pushing engine contract
  messages (agent/time/chat_line/snapshot); browsers auto-reconnect after 3s
  of disconnect
- The live service is driven by mavisframework (Game + Simulator + LiveCompressor)
- Decision export: `decisions.json` (time/role/action/others/importance) for
  governance platforms and expert UI
- Phaser script: the server prefers the local
  `frontend/static/vendor/phaser.min.js` (works offline) and falls back to CDN.
  For offline use, download
  `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js` (~1.3MB)
  into that folder before first run
- Localization: modify the engine's `mavisframework/prompt/scratch.py` and
  frontend copy; no logic changes required

## 9. Custom Maps

1. Follow the maze.py logic in the original generative_agents project to
   support tiled-exported json/csv files
2. Follow the existing maze.json format to merge tiled exports
   (maze_meta_info.json, collision_maze.csv, sector_maze.csv) into a new maze.json
3. Use the map annotation tool: https://github.com/jiejieje/tiled_to_maze.json

## 10. References

- Paper: [Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)
- Code: [mavisframework (self-developed engine)](https://github.com/hellobs/mavis) / [Generative Agents (original)](https://github.com/joonspk-research/generative_agents) / [wounderland](https://github.com/Archermmt/wounderland)
