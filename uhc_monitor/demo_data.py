"""Seed demo data: a SIMULATED older baseline, then an update check of the real PDF via the local test server."""
from dataclasses import replace
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


# What the local test server can answer with, in the order the page offers them: (label, route)
SCENARIOS = [("Good PDF", "/ok.pdf"), ("HTML error page (HTTP 200)", "/html-error"), ("HTML labelled as PDF", "/html-as-pdf"),
             ("Empty body (HTTP 200)", "/empty"), ("Truncated file", "/truncated.pdf"), ("404 Not found", "/missing"),
             ("500 Server error", "/server-error")]
ROUTE_PREFIX = {"beds-mattresses": "", "surgery-knee": testserver.KNEE_PREFIX}


def run_demo_check(store, source, scenario):
    """Run the real update check for `source`, fetching from a throwaway local test server instead of its (unset) URL.
    `scenario` is one of the SCENARIOS labels. Returns the CheckOutcome."""
    if source.id not in ROUTE_PREFIX:
        raise ValueError(f"the test server has no routes for {source.id!r}")
    route = dict(SCENARIOS)[scenario]
    server, base = testserver.start()
    try:
        return run_check(store, replace(source, url=f"{base}{ROUTE_PREFIX[source.id]}{route}"))
    finally:
        server.shutdown()



# Which SIMULATED older version to seed: (label shown on the page, key). Both are built from the existing edit file.
DEMO_VARIANTS = [("Substantive and other changes (the 11 edits)", "all"),
                 ("Cosmetic edits only (just the cosmetic ones from that file)", "cosmetic")]
EDITS_FILE = ROOT / "config" / "simulations" / "beds-mattresses.json"


def variant_edits(variant):
    """The edits of the existing file that make up a variant: every edit, or only those whose kind is 'cosmetic'.
    The edits themselves (and their labels) are never changed."""
    edits = load_edits(EDITS_FILE)
    if variant == "all":
        return edits
    if variant == "cosmetic":
        return [e for e in edits if e["kind"] == "cosmetic"]
    raise ValueError(f"unknown demo variant {variant!r}")


def seed_demo(store, source_id="beds-mattresses", variant="all"):
    """Seed a SIMULATED older version (from extracted text, no PDF) as the baseline, then check the real PDF against it.
    Returns the check outcome ('updated'), or None if this source already has a stored version."""
    if not has_demo(source_id):
        raise ValueError(f"no demo data for {source_id!r}")
    state = store.get_state(source_id)
    if state and state["baseline_version_id"]:     # a failed first check leaves a state row but no version
        return None
    edits = variant_edits(variant)
    real = extract_policy(ROOT / "sources" / "original" / "beds-mattresses.pdf")
    older = make_simulated_older(real, edits)
    vid = store.save_version(source_id, older, simulated=True)
    store.record_check(source_id, INITIALIZED, reason=f"SIMULATED baseline seeded ({variant}: {len(edits)} edits)",
                       version_id=vid, new_baseline_id=vid)
    server, base = testserver.start()
    try:
        return run_check(store, Source(source_id, "Beds and Mattresses", "UnitedHealthcare", f"{base}/ok.pdf"))
    finally:
        server.shutdown()
