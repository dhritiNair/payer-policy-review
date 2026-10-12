"""Findings list flow check (script, not yet pytest): two columns, priority order, one detail at a time.
Seeds a throwaway store with a scripted fake Claude (no live calls).
Run: PYTHONPATH=. .venv/bin/python tests/flow_findings_list.py"""
import json, os, tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["ANTHROPIC_API_KEY"] = ""
root = tempfile.mkdtemp(); os.environ["UHC_STORE_ROOT"] = root
from streamlit.testing.v1 import AppTest
from uhc_monitor import testserver
from uhc_monitor.check import run_check
from uhc_monitor.extract import extract_policy
from uhc_monitor.findings import get_findings, latest_update
from uhc_monitor.llm import ClaudeAssessor
from uhc_monitor.simulate import load_edits, make_simulated_older
from uhc_monitor.sources import INITIALIZED, Source
from uhc_monitor.store import Store

ROOT = Path(__file__).resolve().parent.parent


def check(name, ok): print(("PASS " if ok else "FAIL ") + name); assert ok, name


# --- seed: 4 substantive (one raised by Claude), 3 ambiguous with mixed Claude confidence, one already dismissed
by_id = {e["id"]: e for f in ("beds-mattresses.json", "beds-mattresses-blind.json")
         for e in load_edits(ROOT / "config/simulations" / f)}
D1 = {"id": "D1", "kind": "demo", "op": "replace_text", "section": "Description of Services",
      "find": "Many manufacturers offer beds", "replace": "Some manufacturers offer beds", "description": "inline"}
edits = [by_id[i] for i in ("S1", "S2", "S3", "S4", "A1", "A2", "B5", "C1", "C2", "C3", "C4", "H1")] + [D1]
real = extract_policy(ROOT / "sources/original/beds-mattresses.pdf")
store = Store()
vid = store.save_version("beds-mattresses", make_simulated_older(real, edits), simulated=True)
store.record_check("beds-mattresses", INITIALIZED, reason="SIMULATED baseline seeded", version_id=vid, new_baseline_id=vid)
server, base = testserver.start()
run_check(store, Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", f"{base}/ok.pdf")); server.shutdown()


def answer(label, conf):
    return json.dumps({"suggested_label": label, "confidence": conf, "what_changed": "(scripted)", "interpretation": "(scripted)",
                       "uncertainty": "(scripted)", "suggested_next_step": "(scripted)"})
def create(**kw):
    c = kw["messages"][0]["content"]
    text = (answer("substantive", "medium") if "Many benefit plans" in c else      # A1: Claude raises it
            answer("abstain", "low") if "plan document" in c else                  # A2: abstains
            answer("cosmetic", "high") if "Accessed December" in c else            # B5: cosmetic, high confidence
            answer("cosmetic", "low"))                                             # D1: cosmetic, low confidence
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", model="fake",
                           usage=SimpleNamespace(input_tokens=0, output_tokens=0), _request_id="req_FAKE")
u = latest_update(store, "beds-mattresses")
d = get_findings(store, "beds-mattresses", u["before_version_id"], u["version_id"],
                 ClaudeAssessor(client=SimpleNamespace(messages=SimpleNamespace(create=create)), log_path=Path(root) / "log.jsonl"))
amb = [f for f in d["findings"] if f["final_label"] == "ambiguous"]
d1 = next(f for f in amb if "Some manufacturers" in (f["before_text"] or ""))
store.save_review(d1["key"], "beds-mattresses", u["before_version_id"], u["version_id"], "dismissed", "n/a", None, "ambiguous")
store.db.close()

# --- the page
at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run(); assert not at.exception, at.exception
rows = [b for b in at.button if b.key and b.key.startswith(("row-", "rowsel-"))]
subst = [b for b in rows if "SUBSTANTIVE" in b.label]
ambig = [b for b in rows if "AMBIGUOUS" in b.label]
print("substantive rows:", [b.label.split("\n")[0][:70] for b in subst])
print("ambiguous rows:  ", [b.label.split("\n")[0][:70] + " … " + b.label.split("\n")[-1][-12:] for b in ambig])
check("5 substantive and 3 ambiguous rows", (len(subst), len(ambig)) == (5, 3))
check("Substantive: Claude-escalated finding is first (P1)", "P1" in subst[0].label and "raised by Claude" in subst[0].label)
check("Substantive priorities run P1..P5", [b.label.split("**")[1] for b in subst] == [f"P{i}" for i in range(1, 6)])
check("Ambiguous: abstained (P2) before high-confidence cosmetic (P3)", "P2" in ambig[0].label and "P3" in ambig[1].label)
check("Ambiguous: dismissed finding (P1, Claude low confidence) sinks to the bottom, keeps its number",
      "P1" in ambig[2].label and "DISMISSED" in ambig[2].label)
check("every Ambiguous row carries 'Needs human review'", all("Needs human review" in b.label for b in ambig))
r = subst[1].label
check("row shows priority, label, section, page, changed words, status",
      all(x in r for x in ("P2", "SUBSTANTIVE", "Coverage Rationale > Safety Enclosure with Beds", "p.1", "any → all", "OPEN")))
check("open findings listed before decided ones", [b for b in ambig if "OPEN" in b.label] == ambig[:2])
check("Cosmetic stays collapsed", any(e.label.startswith("Cosmetic changes (4)") for e in at.expander))
check("no detail until a row is clicked", not [b for b in at.button if b.label == "Mark reviewed"])
subst[0].click().run()
check("clicking a row opens its detail with the review form", len([b for b in at.button if b.label == "Mark reviewed"]) == 1
      and any(s.value == "Finding detail" for s in at.subheader))
ambig = [b for b in at.button if b.key and "row" in b.key and "AMBIGUOUS" in b.label]
ambig[0].click().run()
check("opening another row replaces the detail (one at a time)", len([b for b in at.button if b.label == "Mark reviewed"]) == 1
      and any("▼" in b.label and "AMBIGUOUS" in b.label for b in at.button))
[b for b in at.button if b.key and b.key.startswith("rowsel-")][0].click().run()
check("clicking the open row again closes it", not [b for b in at.button if b.label == "Mark reviewed"])
print("\nall findings-list checks passed")
