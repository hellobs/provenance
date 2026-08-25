[简体中文](./README.md) | English

# Provenance — Generative Agent Simulation Platform

A multi-agent simulation platform built on the self-developed
[mavisframework](https://github.com/hellobs/mavis) engine, for demonstrating
"AI value formation is observable and governable" (Global Trust Challenge).
Scenario: investment advisory (secondary market) — agents make decisions, move,
and converse autonomously, with every step configurable, explainable, and
visualizable in real time.

## Architecture

```
Provenance (platform, this repo)
├── provenance/          # platform core
│   ├── live_fastapi.py  # ★ real-time simulation + visualization (FastAPI + WebSocket, single entry)
│   ├── frontend/        # Phaser frontend + texture pool (agents_pool/)
│   ├── scenarios/       # business scenario configs (investment: roles/relations/story)
│   ├── data/            # configs & prompts
│   └── results/         # checkpoints & decision traces (decisions.json)
└── depends on mavisframework  # engine (separate repo hellobs/mavis, installed as wheel)
```

The platform and the engine are separated: **mavisframework** lives in
[hellobs/mavis](https://github.com/hellobs/mavis); this platform depends on it via
`mavisframework==1.0.0` in `requirements.txt`. The role configuration tool
(config_tool) also belongs to the engine repo.

## Quick Start

### 1. Environment

Requires [uv](https://docs.astral.sh/uv/) or [conda](https://docs.conda.io/):

```bash
# with uv (faster)
cd provenance
uv venv .venv --python 3.12
uv pip install -r requirements.txt

# or with conda
conda create -n provenance python=3.12
conda activate provenance
pip install -r requirements.txt
```

> `requirements.txt` depends on `mavisframework==1.0.0`. Build the engine wheel
> first (see next section).

### 2. Install the engine (mavisframework)

```bash
# Option A: build wheel from source and install (recommended, verified stable)
git clone git@github.com:hellobs/mavis.git ../mavis
cd ../mavis && uv build && uv pip install dist/mavisframework-1.0.0-py3-none-any.whl
cd ../provenance

# Option B: editable install (for engine development; note the import quirk
# documented in the engine README)
# uv pip install -e ../mavis
```

### 3. Configure the LLM (choose one)

- **Local Ollama** (free, recommended for development): install
  [Ollama](https://ollama.com/) and pull models
  ```bash
  ollama pull qwen3:4b-instruct-2507-q4_K_M
  ollama pull qwen3-embedding:0.6b-q8_0
  ```
  No config change needed (Ollama is the default).
- **DeepSeek API**: configure `LLM_API_KEY` in `provenance/.env`, and edit
  `provenance/data/config.json` → `agent.think.llm`:
  ```json
  "llm": {
    "provider": "openai",
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": ""
  }
  ```

### 4. Run the live simulation (FastAPI + WebSocket)

```bash
cd provenance/provenance
python live_fastapi.py --name sim-test --start "20250213-09:30" --stride 2 --step 0 --port 5001
```

Open http://127.0.0.1:5001/ in a browser.

### 5. Configure roles (config_tool, in the engine repo)

Roles/relations/story are configured through web forms (no hand-written JSON).
The tool lives in the mavis repo:

```bash
cd ../mavis/config_tool
python app.py
```

Open http://127.0.0.1:5002/ — see `../mavis/config_tool/README.md` for details.

> config_tool writes into this platform's `provenance/frontend/static/assets/village/agents/`
> and `provenance/scenarios/` by default (override with `MAVIS_ASSETS_ROOT` /
> `MAVIS_SCENARIOS_DIR`). Restart the simulation server (5001) after adding roles.

## Run Options

| option | description |
|---|---|
| `--name` | simulation name (unique; checkpoints stored per name) |
| `--start` | starting time |
| `--stride` | game minutes per step (2 for finer detail) |
| `--step` | step count, `0` = run forever |
| `--resume` | resume from a checkpoint |
| `--port` | server port |

## Notes

- Real-time visualization via WebSocket (`/ws`) pushing engine contract messages
  (agent/time/chat_line/snapshot); browsers auto-reconnect after 3s of disconnect
- The live service is driven by mavisframework (Game + Simulator + LiveCompressor)
- Decision export: `decisions.json` (time/role/action/others/importance) for
  governance platforms & expert UI
- Phaser script: the server prefers the local `frontend/static/vendor/phaser.min.js`
  (works offline) and falls back to CDN. For offline use, download
  `https://cdn.jsdelivr.net/npm/phaser@3.55.2/dist/phaser.min.js` (~1.3MB) into
  that folder before first run

## References

### Paper

[Generative Agents: Interactive Simulacra of Human Behavior](https://arxiv.org/abs/2304.03442)

### Code

- [mavisframework (self-developed engine)](https://github.com/hellobs/mavis)
- [Generative Agents (original)](https://github.com/joonspk-research/generative_agents)
- [wounderland](https://github.com/Archermmt/wounderland)
