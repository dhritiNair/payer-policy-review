"""Walk through initialized / unchanged / failed against the local test server, using a throwaway store."""
import shutil
from pathlib import Path

from . import testserver
from .check import run_check
from .sources import Source
from .store import Store

DEMO_ROOT = Path(__file__).resolve().parent.parent / "data" / "demo-store"


def show(step, outcome, store):
    state = store.get_state(outcome.source_id)
    base = store.get_version(state["baseline_version_id"]) if state and state["baseline_version_id"] else None
    base_txt = f"v{base['id']} {base['policy_number']} eff {base['effective_date']} hash {base['content_hash'][:10]}" if base else "none"
    print(f"{step:46} -> {outcome.status:11} baseline: {base_txt}" + (f"   [{outcome.reason}]" if outcome.reason else ""))


def main():
    shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    server, base = testserver.start()
    src = lambda path: Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", f"{base}{path}")
    store = Store(DEMO_ROOT)

    show("1. first check, server returns HTML error", run_check(store, src("/html-error")), store)
    show("2. first good fetch", run_check(store, src("/ok.pdf")), store)
    pdf_files = sorted((DEMO_ROOT / "snapshots" / "beds-mattresses").glob("*.pdf"))
    saved_bytes = pdf_files[0].read_bytes()
    show("3. same document again", run_check(store, src("/ok.pdf")), store)
    show("4. HTTP 200 HTML page", run_check(store, src("/html-error")), store)
    show("5. empty body", run_check(store, src("/empty")), store)
    show("6. 404", run_check(store, src("/missing")), store)
    print(f"   last good copy intact after failures: {pdf_files[0].read_bytes() == saved_bytes} ({pdf_files[0].name})")
    store.db.close()
    store = Store(DEMO_ROOT)   # simulates an app restart
    show("7. after restart, document again", run_check(store, src("/ok.pdf")), store)
    n = store.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    print(f"\nversions stored: {n}  |  check log:")
    for r in store.db.execute("SELECT id, status, version_id, reason FROM checks ORDER BY id"):
        print(f"   #{r['id']} {r['status']:11} version={r['version_id']} {r['reason'] or ''}")
    server.shutdown()


if __name__ == "__main__":
    main()
