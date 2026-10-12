"""Seed demo data: a SIMULATED older baseline, then an update check of the real PDF via the local test server."""
from pathlib import Path

from . import testserver
from .check import run_check
from .extract import extract_policy
from .simulate import load_edits, make_simulated_older
from .sources import INITIALIZED, Source

ROOT = Path(__file__).resolve().parent.parent
DEMO_SOURCES = {"beds-mattresses"}   # policies that have a simulated older version to seed (the knee's comes later)


def has_demo(source_id):
    return source_id in DEMO_SOURCES



def seed_demo(store, source_id="beds-mattresses"):
    """Returns the check outcome, or None if this source already has stored versions."""
    if not has_demo(source_id):
        raise ValueError(f"no demo data for {source_id!r}")
    if store.get_state(source_id):
        return None
    real = extract_policy(ROOT / "sources" / "original" / "beds-mattresses.pdf")
    older = make_simulated_older(real, load_edits(ROOT / "config" / "simulations" / "beds-mattresses.json"))
    vid = store.save_version(source_id, older, simulated=True)
    store.record_check(source_id, INITIALIZED, reason="SIMULATED baseline seeded", version_id=vid, new_baseline_id=vid)
    server, base = testserver.start()
    try:
        return run_check(store, Source(source_id, "Beds and Mattresses", "UnitedHealthcare", f"{base}/ok.pdf"))
    finally:
        server.shutdown()
