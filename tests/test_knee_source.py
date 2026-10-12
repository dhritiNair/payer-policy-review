"""Surgery of the Knee as a second source: registry, test server, extraction facts, and the known extraction gaps."""
import re

import pymupdf
import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import ROOT
from uhc_monitor import testserver
from uhc_monitor.check import run_check
from uhc_monitor.demo_data import has_demo, seed_demo
from uhc_monitor.extract import extract_policy
from uhc_monitor.fetch import fetch_source
from uhc_monitor.sources import FAILED, INITIALIZED, UNCHANGED, Source, load_sources
from uhc_monitor.store import Store

KNEE_PDF = ROOT / "sources" / "original" / "surgery-knee.pdf"


@pytest.fixture(scope="module")
def knee_doc():
    return extract_policy(KNEE_PDF)


@pytest.fixture
def server():
    srv, base = testserver.start()
    yield base
    srv.shutdown()


def knee(base, path):
    return Source("surgery-knee", "Surgery of the Knee", "UnitedHealthcare", f"{base}{testserver.KNEE_PREFIX}{path}")


def test_registry_lists_both_policies_with_urls_not_yet_configured():
    sources = {s.id: s for s in load_sources()}
    assert list(sources) == ["beds-mattresses", "surgery-knee"]
    assert sources["surgery-knee"].name == "Surgery of the Knee" and sources["surgery-knee"].url is None
    assert fetch_source(sources["surgery-knee"]).reason == "no URL configured"


def test_the_source_pdf_is_the_untouched_real_file():
    assert KNEE_PDF.stat().st_size == 402114          # same byte count as the four identical UHC copies


def test_policy_number_and_effective_date_are_read_from_the_text(knee_doc, real_doc):
    assert (knee_doc["policy_number"], knee_doc["effective_date"]) == ("2026T0553II", "2026-06-01")
    assert (real_doc["policy_number"], real_doc["effective_date"]) == ("MP.028.14", "2026-01-01")     # unchanged


def test_unknown_is_the_fallback(tmp_path):
    doc = pymupdf.open(); page = doc.new_page(); page.insert_text((72, 100), "Some policy\nPolicy Number: ???\nEffective Date: someday")
    pdf = tmp_path / "x.pdf"; doc.save(pdf)
    out = extract_policy(pdf)
    assert out["policy_number"] == "Unknown" and out["effective_date"] == "Unknown"


def test_structure_counts(knee_doc):
    assert knee_doc["page_count"] == 10 and len(knee_doc["sections"]) == 21
    assert sum(len(s["paragraphs"]) for s in knee_doc["sections"]) == 95
    h1 = [s["heading"] for s in knee_doc["sections"] if s["level"] == 1]
    assert h1 == ["Application", "Coverage Rationale", "Medical Records Documentation Used for Reviews", "Definitions",
                  "Applicable Codes", "Description of Services", "Clinical Evidence", "U.S. Food and Drug Administration (FDA)",
                  "References", "Policy History/Revision Information", "Instructions for Use"]


def test_no_arrow_markers_and_page_break_paragraphs_are_joined(knee_doc):
    paras = [p for s in knee_doc["sections"] for p in s["paragraphs"]]
    assert not any("" in p["text"] or "→" in p["text"] for p in paras)
    assert sum(1 for p in paras if p["page_end"] > p["page"]) == 3


def test_history_table_is_raw_text_and_excluded(knee_doc):
    hist = next(s for s in knee_doc["sections"] if s["heading"].startswith("Policy History"))
    assert hist["excluded_from_diff"] and any("06/01/2026" in p["text"] for p in hist["paragraphs"])


def test_cpt_and_hcpcs_codes_are_structured_code_rows(knee_doc):
    """43 codes: 40 CPT (numeric and 0737T-style) + 3 HCPCS, all structured; column headers dropped; footnote kept.
    The PDF's own metadata has a typo (27568/27569) where the body table lists 27658/27659 (flexor tendon repair; the
    descriptions match those codes), so the body text is taken as the source of truth."""
    ac = next(s for s in knee_doc["sections"] if s["heading"] == "Applicable Codes")
    rows = ac["code_table"]
    assert len(rows) == 43 and len({r["code"] for r in rows}) == 43
    assert [r["code"] for r in rows if r["code"][0].isalpha()] == ["G0428", "J7330", "S2112"]
    assert sum(1 for r in rows if re.fullmatch(r"\d{5}|\d{4}T", r["code"])) == 40
    assert all(r["description"] and r["page"] in (3, 4) for r in rows)
    assert rows[0] == {"code": "0737T", "description": "Xenograft implantation into the articular surface", "page": 3}
    assert len(ac["paragraphs"]) == 2 and ac["paragraphs"][0]["text"].startswith("The following list(s) of procedure")
    assert ac["paragraphs"][1]["text"].startswith("CPT® is a registered trademark")
    assert not any(re.fullmatch(r"(CPT|HCPCS) Code\s+Description", p["text"]) for s in knee_doc["sections"] for p in s["paragraphs"])
    meta = pymupdf.open(KNEE_PDF).metadata["subject"].split("Applicable Procedure Codes:")[-1]
    expected = set(re.findall(r"\b(?:[A-Z]\d{4}|\d{5}|\d{4}[A-Z])\b", meta))
    got = {r["code"] for r in rows}
    assert len(expected) == 43 and expected - got == {"27568", "27569"} and got - expected == {"27658", "27659"}


def test_paragraphs_are_not_split_mid_sentence(knee_doc):
    for s in knee_doc["sections"]:
        for p in s["paragraphs"][1:]:
            assert not p["text"][:1].islower(), p["text"][:60]
    text = " ".join(p["text"] for s in knee_doc["sections"] for p in s["paragraphs"])
    for joined in ("are needed before meniscal scaffolds can be recommended",
                   "its reliance on predominantly low-level evidence",
                   "limited by its short follow-up duration and very high early failure rate",
                   "to support meaningful conclusions about xenograft effectiveness."):
        assert joined in text


def test_the_test_server_serves_the_knee_and_its_failure_scenarios(server):
    r = fetch_source(knee(server, "/ok.pdf"))
    assert r.ok and r.page_count == 10
    for path in ("/html-error", "/html-as-pdf", "/empty", "/truncated.pdf", "/missing", "/server-error"):
        assert fetch_source(knee(server, path)).status == FAILED, path
    assert fetch_source(Source("beds-mattresses", "x", "UnitedHealthcare", f"{server}/ok.pdf")).page_count == 4   # beds unchanged


def test_two_policies_keep_separate_state_in_one_store(server):
    store = Store()
    beds = Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", f"{server}/ok.pdf")
    assert run_check(store, knee(server, "/ok.pdf")).status == INITIALIZED
    assert run_check(store, beds).status == INITIALIZED
    assert run_check(store, knee(server, "/html-error")).status == FAILED
    assert run_check(store, beds).status == UNCHANGED                   # a knee failure does not touch the beds state
    assert run_check(store, knee(server, "/ok.pdf")).status == UNCHANGED
    versions = {r["source_id"]: r["policy_number"] for r in store.db.execute("SELECT source_id, policy_number FROM versions")}
    assert versions == {"surgery-knee": "2026T0553II", "beds-mattresses": "MP.028.14"}


def test_demo_data_exists_only_for_beds_and_cannot_be_seeded_under_the_knee():
    assert has_demo("beds-mattresses") and not has_demo("surgery-knee")
    with pytest.raises(ValueError):
        seed_demo(Store(), "surgery-knee")
    assert Store().get_state("surgery-knee") is None


@pytest.mark.ui
def test_selector_shows_both_policies_and_the_knee_page_has_no_beds_demo_button(app_env):
    app = str(ROOT / "app.py")
    at = AppTest.from_file(app, default_timeout=60).run(); assert not at.exception, at.exception
    sel = next(s for s in at.selectbox if s.label == "Policy")
    assert sel.options == ["Beds and Mattresses", "Surgery of the Knee"] and [t.value for t in at.title] == ["Beds and Mattresses"]
    sel.select_index(1).run(); assert not at.exception, at.exception
    assert [t.value for t in at.title] == ["Surgery of the Knee"]
    assert not [b for b in at.button if b.label == "Load demo data"]
    assert any("no demo data for this policy yet" in i.value for i in at.info)
    assert Store().get_state("surgery-knee") is None                      # nothing was seeded


def test_knee_independent_edit_set_applies_and_builds_a_labelled_simulated_older_version(knee_doc):
    import hashlib
    import json
    from uhc_monitor.classify import compare
    from uhc_monitor.simulate import SIMULATED_LABEL, load_edits, make_simulated_older
    path = ROOT / "config" / "simulations" / "surgery-knee-independent.json"
    data = json.loads(path.read_text())
    edits = load_edits(path)
    assert data["source"] == "surgery-knee" and [e["id"] for e in edits] == [f"K{i}" for i in range(1, 13)]
    assert {e["kind"] for e in edits} == {"unlabeled"}                    # ground truth is held by the user, not in the file
    older = make_simulated_older(knee_doc, edits)                          # every target is found exactly once, or this raises
    assert older["simulated"] is True and older["simulation"]["label"] == SIMULATED_LABEL and older["source_file"] is None
    assert (older["policy_number"], older["effective_date"]) == ("2026T0553II", "2026-06-01")
    assert len(compare(older, knee_doc)) == 11                              # K12 is a pure reorder: noted, not reported
    saved = json.loads((ROOT / "results" / "knee-independent-initial-run.json").read_text())
    assert saved["source"] == "surgery-knee" and len(saved["results"]) == 12
    assert saved["real_pdf_sha256"] == hashlib.sha256(KNEE_PDF.read_bytes()).hexdigest()    # the run is tied to this exact PDF
