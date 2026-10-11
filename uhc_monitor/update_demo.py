"""Seed a SIMULATED older baseline, then check the real PDF and watch the status become 'updated'."""
import copy
import shutil
from pathlib import Path

from . import testserver
from .check import run_check
from .extract import extract_policy
from .simulate import load_edits, make_simulated_older
from .sources import INITIALIZED, Source
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = ROOT / "data" / "demo-store"


def describe(store, vid):
    v = store.get_version(vid)
    tag = "SIMULATED" if v["simulated"] else "real"
    return f"v{vid} [{tag}] {v['policy_number']} eff {v['effective_date']} hash {v['content_hash'][:10]}"


def line(label, o, store):
    before = f" before={describe(store, o.before_version_id)}" if o.before_version_id else ""
    print(f"{label:44} -> {o.status:9} baseline={describe(store, o.baseline_version_id)}{before}" + (f"  [{o.reason}]" if o.reason else ""))


def main():
    shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    server, base = testserver.start()
    src = lambda path: Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", f"{base}{path}")
    store = Store(DEMO_ROOT)

    real = extract_policy(ROOT / "sources" / "original" / "beds-mattresses.pdf")
    edits = load_edits(ROOT / "config" / "simulations" / "beds-mattresses.json")
    older = make_simulated_older(real, edits)
    vid = store.save_version("beds-mattresses", older, simulated=True)
    store.record_check("beds-mattresses", INITIALIZED, reason="SIMULATED baseline seeded", version_id=vid, new_baseline_id=vid)
    print(f"Seeded {describe(store, vid)} with {len(edits)} edits:")
    for e in edits:
        print(f"   {e['id']:3} {e['kind']:12} {e['description']}")
    print()

    line("1. real PDF vs SIMULATED baseline", run_check(store, src("/ok.pdf")), store)
    line("2. same real PDF again", run_check(store, src("/ok.pdf")), store)

    def history_only_change(data):
        doc = copy.deepcopy(extract_policy(data))
        for s in doc["sections"]:
            if s.get("excluded_from_diff"):
                s["paragraphs"][0]["text"] += " (edited)"
        return doc
    line("3. only the History table differs", run_check(store, src("/ok.pdf"), extract=history_only_change), store)
    line("4. server returns HTML error page", run_check(store, src("/html-error")), store)
    line("5. real PDF again after the failure", run_check(store, src("/ok.pdf")), store)
    store.db.close()
    store = Store(DEMO_ROOT)
    line("6. after restart, real PDF again", run_check(store, src("/ok.pdf")), store)

    print("\nversions stored:")
    for v in store.db.execute("SELECT id, simulated, effective_date, pdf_path FROM versions ORDER BY id"):
        print(f"   v{v['id']} {'SIMULATED (no PDF)' if v['simulated'] else 'real, PDF saved'} eff {v['effective_date']}")
    print("check log:")
    for r in store.db.execute("SELECT * FROM checks ORDER BY id"):
        print(f"   #{r['id']} {r['status']:11} before={r['before_version_id']} after={r['version_id']} {r['reason'] or ''}")
    server.shutdown()


if __name__ == "__main__":
    main()
