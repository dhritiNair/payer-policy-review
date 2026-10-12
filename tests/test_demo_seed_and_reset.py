"""Seeding a SIMULATED older version (all 11 edits, or only the cosmetic ones) and resetting one policy's demo data."""
import hashlib
import json

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import ROOT
from uhc_monitor import testserver
from uhc_monitor.check import run_check
from uhc_monitor.classify import COSMETIC
from uhc_monitor.demo_data import DEMO_VARIANTS, EDITS_FILE, run_demo_check, seed_demo, variant_edits
from uhc_monitor.findings import get_findings, latest_update
from uhc_monitor.llm import ClaudeAssessor
from uhc_monitor.simulate import SIMULATED_LABEL
from uhc_monitor.sources import Source
from uhc_monitor.store import Store

APP = str(ROOT / "app.py")
EDITS_SHA256 = "55f44943773967ed9b6f21765e69760c337004b5195b0ca5f37e4fd41d542b61"      # config/simulations/beds-mattresses.json
LABEL_ALL = "Substantive and other changes (the 11 edits)"
LABEL_COSMETIC = "Cosmetic edits only (just the cosmetic ones from that file)"
BEDS = Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", None)
KNEE = Source("surgery-knee", "Surgery of the Knee", "UnitedHealthcare", None)


def open_app():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def click(at, label=None, key=None):
    btn = next(b for b in at.button if (b.label == label if label else b.key == key))
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]


def seed_via_page(at, label):
    at.selectbox(key="demo-variant-beds-mattresses").select(label)
    click(at, "Seed older version")


def metrics(at):
    return {m.label: m.value for m in at.metric}


# --- the two variants ------------------------------------------------------------------------------------------------

def test_the_choices_and_the_edits_behind_them():
    assert [label for label, _ in DEMO_VARIANTS] == [LABEL_ALL, LABEL_COSMETIC]
    assert hashlib.sha256(EDITS_FILE.read_bytes()).hexdigest() == EDITS_SHA256            # no edit or label was changed
    allv, cos = variant_edits("all"), variant_edits("cosmetic")
    assert len(allv) == 11                                                               # the label says 11, so check it
    assert [e["id"] for e in cos] == ["C1", "C2", "C3", "C4"] and {e["kind"] for e in cos} == {"cosmetic"}
    assert cos == [e for e in allv if e["kind"] == "cosmetic"]                            # identical dicts, just filtered
    with pytest.raises(ValueError):
        variant_edits("something else")


@pytest.mark.parametrize("variant, n_edits", [("all", 11), ("cosmetic", 4)])
def test_a_variant_is_a_labelled_simulated_version_with_no_new_pdf_and_checks_as_updated(variant, n_edits):
    store = Store()
    out = seed_demo(store, "beds-mattresses", variant)
    assert out.status == "updated" and "SIMULATED" in out.reason
    older = store.get_version(out.before_version_id)
    assert older["simulated"] == 1 and older["pdf_path"] is None
    manifest = store.load_extracted(older["id"])
    assert manifest["simulated"] is True and manifest["simulation"]["label"] == SIMULATED_LABEL
    assert len(manifest["simulation"]["edits"]) == n_edits and manifest["source_file"] is None
    pdfs = list((store.root / "snapshots" / "beds-mattresses").glob("*.pdf"))
    assert len(pdfs) == 1 and pdfs[0].read_bytes() == (ROOT / "sources/original/beds-mattresses.pdf").read_bytes()   # only the real PDF
    assert run_demo_check(store, BEDS, "Good PDF").status == "unchanged"


def test_the_cosmetic_only_update_has_four_cosmetic_findings_and_nothing_to_review():
    store = Store(); seed_demo(store, "beds-mattresses", "cosmetic")
    u = latest_update(store, "beds-mattresses")
    calls = []
    assessor = ClaudeAssessor(client=type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **k: calls.append(1))})})())
    data = get_findings(store, "beds-mattresses", u["before_version_id"], u["version_id"], assessor)
    assert sorted(f["rule_id"] for f in data["findings"]) == ["COS-FORMAT", "COS-FORMAT", "COS-FORMAT", "COS-TOC"]
    assert {f["final_label"] for f in data["findings"]} == {COSMETIC} and data["ambiguous_count"] == 0 and not calls


# --- the page --------------------------------------------------------------------------------------------------------

def test_the_page_offers_the_two_choices_in_place_of_load_demo_data():
    at = open_app()
    sel = at.selectbox(key="demo-variant-beds-mattresses")
    assert sel.label == "Older (simulated) version to seed" and sel.options == [LABEL_ALL, LABEL_COSMETIC] and sel.value == LABEL_ALL
    assert "Load demo data" not in [b.label for b in at.button] and "Seed older version" in [b.label for b in at.button]
    assert Store().get_state("beds-mattresses") is None                                   # choosing seeds nothing


@pytest.mark.ui
def test_seeding_the_11_edits_shows_the_usual_findings():
    at = open_app(); seed_via_page(at, LABEL_ALL)
    m = metrics(at)
    assert (m["Substantive"], m["Ambiguous — Needs human review"], m["Cosmetic"], m["Total findings"]) == ("4", "2", "4", "6")
    assert any(w.value.startswith("**Beds and Mattresses: updated**") for w in at.warning)


@pytest.mark.ui
def test_seeding_the_cosmetic_edits_shows_the_update_with_nothing_to_review_and_the_changes_listed():
    at = open_app(); seed_via_page(at, LABEL_COSMETIC)
    assert any(w.value.startswith("**Beds and Mattresses: updated**") for w in at.warning)
    m = metrics(at)
    assert (m["Substantive"], m["Ambiguous — Needs human review"], m["Cosmetic"], m["Resolved by reviewer"]) == ("0", "0", "4", "0")
    assert (m["Open"], m["Reviewed"], m["Dismissed"], m["Total findings"]) == ("0", "0", "0", "0")
    assert any("No findings need review: all 4 changes in this update are cosmetic" in i.value for i in at.info)
    assert not [b for b in at.button if b.key and b.key.startswith(("row-", "rowsel-"))]          # no rows to review
    assert not any("Select a finding above" in c.value for c in at.caption)                       # and no prompt to select one
    assert any(x.value == "**Version compared**  \nv1 SIMULATED → v2 real" for x in at.markdown)
    assert any(x.value.startswith("**Older (simulated):** v1") for x in at.markdown)
    exp = next(e for e in at.expander if e.label.startswith("Cosmetic changes (4)"))
    listed = [x.value for x in exp.markdown if "· rule **COS-" in x.value]
    assert len(listed) == 4 and sum("COS-TOC" in x for x in listed) == 1 and sum("COS-FORMAT" in x for x in listed) == 3
    assert exp.proto.expanded                                                                  # open, so the list is visible


# --- reset -----------------------------------------------------------------------------------------------------------

def test_reset_clears_only_the_selected_policy_and_only_after_confirmation(tmp_path):
    store = Store(); seed_demo(store, "beds-mattresses", "all"); run_demo_check(store, KNEE, "Good PDF")
    u = latest_update(store, "beds-mattresses")
    d = get_findings(store, "beds-mattresses", u["before_version_id"], u["version_id"], ClaudeAssessor(client=None))
    store.save_review(d["findings"][0]["key"], "beds-mattresses", u["before_version_id"], u["version_id"], "reviewed", "n", None, "x")
    count = lambda t, sid: Store().db.execute(f"SELECT COUNT(*) FROM {t} WHERE source_id=?", (sid,)).fetchone()[0]
    knee_before = {t: count(t, "surgery-knee") for t in ("versions", "checks", "source_state")}
    store.db.close()

    at = open_app()
    assert "Reset demo" in [b.label for b in at.button]
    click(at, "Reset demo")                                                                    # step 1: only asks
    assert any("deletes everything stored for **Beds and Mattresses**" in w.value for w in at.warning)
    assert {"Yes, delete the demo data for this policy", "Cancel"} <= {b.label for b in at.button}
    assert count("versions", "beds-mattresses") == 2 and count("reviews", "beds-mattresses") == 1       # nothing deleted yet
    click(at, "Cancel")
    assert "Reset demo" in [b.label for b in at.button] and count("versions", "beds-mattresses") == 2
    click(at, "Reset demo")
    click(at, "Yes, delete the demo data for this policy")                                     # step 2: deletes

    assert all(count(t, "beds-mattresses") == 0 for t in ("versions", "checks", "source_state", "reviews"))
    root = Store().root
    assert not (root / "snapshots" / "beds-mattresses").exists()
    assert not list((root / "findings").glob("beds-mattresses_*.json"))
    assert {t: count(t, "surgery-knee") for t in knee_before} == knee_before                   # the other policy is untouched
    assert (root / "snapshots" / "surgery-knee").exists()
    assert any("Cleared the demo data for Beds and Mattresses (removed: versions 2, checks 2, saved reviews 1)" in s.value for s in at.success)
    assert "Seed older version" in [b.label for b in at.button] and "Reset demo" not in [b.label for b in at.button]
    seed_via_page(at, LABEL_COSMETIC)                                                          # and it can be seeded again, differently
    assert metrics(at)["Cosmetic"] == "4"


def test_reset_is_only_offered_when_something_is_stored():
    at = open_app()
    assert "Reset demo" not in [b.label for b in at.button]
    click(at, key="run-check-beds-mattresses")
    assert "Reset demo" in [b.label for b in at.button]


def test_store_reset_source_removes_only_that_policys_rows_and_files():
    s = Store(); seed_demo(s, "beds-mattresses", "all"); run_demo_check(s, KNEE, "Good PDF")
    (s.root / "findings").mkdir(exist_ok=True)
    (s.root / "findings" / "surgery-knee_1_2.json").write_text("{}")                          # another policy's cache file
    (s.root / "findings" / "beds-mattresses-extra_1_2.json").write_text("{}")                  # a similar-looking name must survive
    removed = s.reset_source("beds-mattresses")
    assert removed["versions"] == 2 and removed["checks"] == 2
    assert s.get_state("beds-mattresses") is None and s.get_state("surgery-knee") is not None
    assert (s.root / "findings" / "surgery-knee_1_2.json").exists() and (s.root / "findings" / "beds-mattresses-extra_1_2.json").exists()
    assert s.reset_source("beds-mattresses") == {"versions": 0, "checks": 0, "reviews": 0}      # safe to repeat
