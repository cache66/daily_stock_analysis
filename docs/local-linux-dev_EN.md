# Local Linux Development Setup

This document shows how to prepare a repo-local Linux environment and start either the API or the main program.

## When To Use This

- You develop this repository on Linux or WSL
- You want the virtual environment to live inside the repository
- You want one script entry point for install, smoke checks, and startup

## Recommended Environment Location

Use the repo-local virtual environment:

```bash
.venv-linux/
```

This keeps the runtime tied to the current checkout and avoids relying on `/tmp`, global site-packages, or paths copied from another machine.

## One-Time Setup

From the repository root, run:

```bash
./scripts/run-local-linux.sh install
```

The script will:

- create `.venv-linux`
- upgrade `pip`, `setuptools`, and `wheel`
- install `requirements.txt`
- default `LITELLM_LOCAL_MODEL_COST_MAP=true` so Linux startup does not stall on LiteLLM remote model-cost-map initialization

If you do not already have a config file, create one:

```bash
cp .env.example .env
```

## Smoke Check Before Startup

Run a lightweight FastAPI import smoke:

```bash
./scripts/run-local-linux.sh check
```

This is useful for quickly confirming that dependencies, routing, and app initialization are basically healthy.

## Start The API

Default host and port:

```bash
./scripts/run-local-linux.sh api
```

Custom host and port:

```bash
./scripts/run-local-linux.sh api 0.0.0.0 8010
```

Example health checks:

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/auth/status
```

## Run The Main Program

Show CLI help:

```bash
./scripts/run-local-linux.sh main --help
```

Example dry-run:

```bash
./scripts/run-local-linux.sh main --stocks 600519 --no-market-review --dry-run
```

## Notes

- If `.env` is missing, the script warns but does not generate secrets or real configuration.
- Some third-party libraries may print warnings during startup; treat process startup success as the real signal.
- `run-local-linux.sh` exports `LITELLM_LOCAL_MODEL_COST_MAP=true` by default so local Linux startup prefers LiteLLM's bundled cost map instead of blocking on a remote fetch. If you explicitly need the remote map, set `LITELLM_LOCAL_MODEL_COST_MAP=false` before running the script.
- On a new Linux machine, recreate the environment with `install` instead of copying an old virtualenv directory from another host.

## Manual Alternative

If you prefer not to use the script:

```bash
python3 -m venv .venv-linux
source .venv-linux/bin/activate
export LITELLM_LOCAL_MODEL_COST_MAP=true
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```
