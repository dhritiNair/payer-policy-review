# UHC Policy Change Monitor

A Streamlit app that helps a revenue-cycle reviewer monitor changes to UnitedHealthcare policy documents.
Work in progress, built step by step; this section covers setup only.

## Setup

Requires Python 3.11 or newer.

```bash
cd takehome
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Use the project's own virtual environment for everything. Run Streamlit as
`.venv/bin/python -m streamlit run app.py` (not a bare `streamlit`), so it cannot pick up another Python install.

### Claude API key (optional)

The app works without a key: changes are classified by rules only, and anything the rules cannot judge is shown as
**Ambiguous (Needs human review)**. With a key, Claude is asked for advice on those ambiguous changes. It can
escalate a change to substantive but never dismiss one.

The key is read from the `ANTHROPIC_API_KEY` environment variable. Either:

1. export it in your shell: `export ANTHROPIC_API_KEY=...`, or
2. copy `.env.example` to `.env` and fill in the value. `.env` is gitignored and must never be committed.

An exported variable takes precedence over `.env`. Nothing in this project prints, logs or stores the key.

Check that the key and model work (prints only `PASS` or `FAIL`):

```bash
.venv/bin/python -m uhc_monitor.check_claude
```

Model and settings are in `config/llm.json` (default model `claude-sonnet-5-5`).

## Run the app

```bash
.venv/bin/python -m streamlit run app.py
```

Until the real UHC URL is configured in `config/sources.json`, use the **Load demo data** button: it stores a
SIMULATED older version as the baseline and checks the real PDF served by a local test server.
