# Payer Policy Review

**Purpose:** A revenue-cycle reviewer checks UnitedHealthcare policy documents for meaningful changes, inspects each against its source, records a decision, and reruns without losing work or creating duplicate alerts.
**Audience:** The Ember reviewer, then a future maintainer.
**Owner:** Dhriti Nair, dhritinai@gmail.com

Small samples throughout: results are diagnostic, not proof of production accuracy. Most edits were written by us.

## Status

**Works**
- Update checks: a bad download (empty file, 404/500 error, HTML error page, cut-off file) shows as "failed" and keeps the last good copy. It is never shown as "unchanged."
- Each policy version is saved, so reruns compare against the last one.
- Text is extracted with page and section references, so every finding points back to the PDF.
- Changes are labeled cosmetic, substantive or "needs human review," with the rule and the evidence shown. Claude gives a second opinion on the "needs review" items only.
- Reviewer decisions survive a restart. Rerunning an unchanged policy adds no duplicate findings, and a new revision reopens the review.
- The Streamlit app has "Run update check" and "Reset demo" buttons.
- 144 tests pass.

**Simulated**
- Older policy versions: made by editing the real PDF's extracted text, labeled SIMULATED, no new PDFs.
- The policy website: a local test server stands in for UHC.

**Not yet tested**
- Fetching from the real UHC URLs (left blank in config).
- Demo seeding for Surgery of the Knee (only the update check runs there).
- Ember's own unlabeled examples (not received yet).

## Run it in 5 minutes
From the repo root:
1. `python3 -m venv .venv`
2. `.venv/bin/pip install -r requirements.txt`
3. Optional: copy `.env.example` to `.env` and add `ANTHROPIC_API_KEY` (rules-only works without it). Check: `.venv/bin/python -m uhc_monitor.check_claude`.
4. Run the tests: `.venv/bin/python -m pytest`
5. Start the app: `.venv/bin/python -m streamlit run app.py`

Evaluation: `.venv/bin/python -m uhc_monitor.evaluate [folder of edit sets]`. Each `*.json` is an edit set, with optional `NAME.labels.json`.

## Repo map
Where things are in the repo:
- `uhc_monitor/`: the code.
- `config/`: settings. `sources.json` lists the policies and their URLs, `llm.json` holds the Claude settings, and `simulations/` holds the simulated edit sets.
- `sources/original/`: the real UHC PDFs.
- `results/`: evaluation runs and scores.
- `tests/`: the test suite.
- `data/`: created when the app runs (the database, extracted text and Claude call logs). It is not in git.

## How it works
Fetch the known URL and validate it is a PDF; extract text, sections and the code table; store a snapshot in SQLite (`data/store/monitor.db`: versions, source_state, checks, reviews) plus files; diff and classify with ordered rules (cosmetic, substantive, or ambiguous "Needs human review"); Claude advises on ambiguous items only and can only escalate to substantive, never dismiss. Reviewer decisions are saved.

## Key decisions
- Simulated versions, because only one real version exists.
- Rules first, then Claude, for explainability; escalate-only, so a model can never hide a change.
- The reviewer label shows on the page; the tool label is kept for evaluation.
- The History table is excluded from diffs because it logs past revisions.

## Results
A MISS is a truly substantive edit the tool labeled cosmetic or ignored; edits sent to a human are counted separately.
- **Independent Beds set N1-N12** (separate session, key withheld, run before tuning): 6 substantive per key (N2, N4, N5, N7, N9, N11); rules alone labeled 5 substantive, 0 misses, 0 false positives. N9 ("is required" to "may be required") went to human review, as did N3, N8, N12; N1, N6, N10 were cosmetic or ignored.
- **Blind 7 + original 11 + independent 12:** 12 of 13 substantive found by rules alone, 0 misses, 0 false positives. Original 11: 4 substantive, all found; human review A1, A2; cosmetic C1-C4 and H1; 0 misses, 0 false positives.
- **Blind 7 (B-ids):** before the three fixes (autojunk, move handling, modal tiers), 3 of 3 substantive found, 1 false positive (B3), 3 avoidable human reviews (B4-B6). After: 3 of 3, 0 false positives, B4 quiet, B3 correctly ambiguous, B5 and B6 still go to a human.
- **Knee set K1-K12** (6 substantive, 4 cosmetic, 2 ambiguous per key): rules found 4 of 6, 0 misses, 0 false positives; K6 (WOMAC threshold, in Definitions) and K10 (evidence conclusion flipped) went to a human, and Claude raised both. One rule change (Definitions added to coverage sections; own commit, regression test) now gives 5 of 6, 0 misses, 0 false positives (the Definitions rule change was made after seeing K6's unscored result, before the key was used); rules now label K1, K2, K5, K6, K8 substantive; K7, K10, K11 go to human review; K3, K4, K9, K12 cosmetic. K10 is the one true-substantive edit still sent to a human. The knee answer key is now in the repo as `config/simulations/surgery-knee-independent.labels.json` and is a scored regression set (rules alone: initial 4 of 6, after the Definitions change 5 of 6), since the change followed a failure. Beds results were identical before and after.
- **Claude on, across sets:** 3 escalations on Beds, 1 matched the key. It suggested cosmetic on 6 of 9 ambiguous items, which stay in the review queue. Its self-reported confidence is not calibrated (said high on N3).

**Causes of remaining errors:** N9, the modal rule sends any may/should change to a human; N12, a paragraph added outside coverage sections only gets the default rule; K10 (key: substantive) and K11 (key: ambiguous), Clinical Evidence page 8 and FDA page 8, both get AMB-DEFAULT "outside the coverage sections"; B5 and B6 (accessed-date, who/that), no rule recognizes them as harmless.

## Model logging
claude-sonnet-5-5, prompt v2, effort medium; logged per call with prompt hash.

## Limitations: what breaks first
1. Live fetch against real UHC URLs (untested).
2. Rule word lists and the coverage-section list on a new policy format.

Also: rules and word lists are our own design, not expert-validated; the Claude prompt lists changed words as "Words removed/added" and once read them as counts; an effective-date-only change comes out ambiguous; unknown dates and plans show as "Unknown"; the body code table is the one source of truth for codes (the knee PDF metadata has a code typo).

## Next two things
1. Run Ember's unlabeled examples and real UHC revisions before tuning; add real source URLs.
2. Have a revenue-cycle expert review the rule lists; consider an audit list for Claude-resolved cosmetic items.

## Out of scope
Other payers, crawling, deployment, auth, notifications, claims data and dollar estimates (claims-impact dashboard parked).

## Brief check
Detect and classify: `uhc_monitor/`. Evidence and decisions: the app, reviews in `data/store/monitor.db`. Scores and limits: `results/` and Results above.
