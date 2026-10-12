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

## Running the tests

```bash
.venv/bin/python -m pytest -q                 # everything (about 20 seconds)
.venv/bin/python -m pytest -q -m "not ui"     # skip the tests that drive the Streamlit page headlessly
```

Tests use throwaway state (a temporary store and Claude log, and an empty API key), so they never touch `data/`, never
call the Claude API, and need no network. They cover: text extraction, the classifier rules and the fixes made after
probing (long-paragraph pairing, moved paragraphs, modal-word handling), fetch validation and statuses (a failed or
non-PDF download is `failed`, never `unchanged`, and keeps the last good copy), version identity and duplicate alerts,
saved reviews, the Claude fallback (advisory and escalate-only, logged, never leaks the key), the page itself (header,
findings list, review flow, reviewer labels) and the evaluation.

## Running the evaluation

One command scores the classifier on every example set in a folder (default `config/simulations/`):

```bash
.venv/bin/python -m uhc_monitor.evaluate                           # the bundled sets
.venv/bin/python -m uhc_monitor.evaluate path/to/new-folder        # any folder of examples
```

**Folder format.** Each `*.json` file in the folder is an edit set, in the same format as
`config/simulations/beds-mattresses-blind.json`: a list of edits that turn the real policy text into a SIMULATED older
version (no PDF is created). Ground truth comes from an optional sibling file `NAME.labels.json`
(`{"labels": {"<edit id>": "substantive | cosmetic | ambiguous | excluded"}}`), or from each edit's own `kind`. Edits
with no label (`"kind": "unlabeled"` and no labels file) are classified and listed but not scored, so independent
examples can be run first and scored later from a saved run:

```bash
.venv/bin/python -m uhc_monitor.evaluate --saved results/NAME-run.json --labels NAME.labels.json --out results/NAME-score
```

**What it reports**, per set and in total: correct substantive detections (automatic), misses, false positives, and
cases sent to human review, with automatic detection counted separately from abstention. Human review is split into
"as intended" (the key says the edit is genuinely ambiguous), "true-substantive not auto-detected" and "true-cosmetic,
avoidable". For every error or avoidable review it shows the source evidence (before/after text around the change with
the changed words marked, page references, matched rule) and a likely cause.

**What it logs** so a run can be reproduced: time, code commit (and whether there were uncommitted changes), the rule
configuration, the Claude model, prompt version and effort from `config/llm.json` (the evaluation itself never calls
Claude, so those are recorded for reference), and a SHA-256 of every edit and labels file and of the real policy.

**Where results go.** `results/NAME.json` and `results/NAME.txt` (default name `eval-<folder>-<timestamp>`). An
existing result is never overwritten unless you pass `--force`, so an initial, before-tuning result cannot be replaced
by accident. For independent examples: run them, save the results, commit them, and only then change any rules.

**Official results so far** (rules only, small samples, diagnostic only):

| Set | Run | File |
|---|---|---|
| Independent N1-N12 | before any tuning (official) | `results/independent-initial-score.txt` |
| Blind B1-B7 | before the three classifier fixes / after them | `results/blind-initial-score.txt`, `results/blind-after-fixes-score.txt` |
| Original S/A/C/H 1-11 | before / after the fixes | `results/original-initial-score.txt`, `results/original-after-fixes-score.txt` |
| All three sets | current rules | `results/eval-current-rules.txt` |

These samples are tiny and the rules were tuned against some of them (the blind set after the fact), so treat the
numbers as a diagnostic of where the rules behave well or badly, not as an accuracy figure. Only the independent set
was scored before any tuning.
