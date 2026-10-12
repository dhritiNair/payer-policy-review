"""Shared test seed: a throwaway store with a scripted fake Claude (no live calls).
4 substantive by rules + 1 raised by Claude (A1), 3 ambiguous (A2 abstained, B5 cosmetic/high, D1 cosmetic/low, D1 pre-dismissed)."""
import json
from pathlib import Path
from types import SimpleNamespace

from uhc_monitor import testserver
from uhc_monitor.check import run_check
from uhc_monitor.extract import extract_policy
from uhc_monitor.findings import get_findings, latest_update
from uhc_monitor.llm import ClaudeAssessor
from uhc_monitor.simulate import load_edits, make_simulated_older
from uhc_monitor.sources import INITIALIZED, Source
from uhc_monitor.store import Store

ROOT = Path(__file__).resolve().parent.parent


def seed(root):
    """Seed the store at `root`; returns {"A1": key, "A2": key, "B5": key, "D1": key, "S1": key, ...} for the findings."""
    by_id = {e["id"]: e for f in ("beds-mattresses.json", "beds-mattresses-blind.json")
             for e in load_edits(ROOT / "config/simulations" / f)}
    D1 = {"id": "D1", "kind": "demo", "op": "replace_text", "section": "Description of Services",
          "find": "Many manufacturers offer beds", "replace": "Some manufacturers offer beds", "description": "inline"}
    edits = [by_id[i] for i in ("S1", "S2", "S3", "S4", "A1", "A2", "B5", "C1", "C2", "C3", "C4", "H1")] + [D1]
    real = extract_policy(ROOT / "sources/original/beds-mattresses.pdf")
    store = Store(root)
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

    keys = {}
    for f in d["findings"]:
        text = (f["before_text"] or "") + (f["after_text"] or "")
        for name, needle in (("A1", "Many benefit plans"), ("A2", "plan document"), ("B5", "Accessed December"),
                             ("D1", "Some manufacturers"), ("S1", "any of the following"), ("S2", "Medications to address"),
                             ("S3", "E0911"), ("S4", "includes certain support")):
            if needle in text:
                keys[name] = f["key"]
    return keys
