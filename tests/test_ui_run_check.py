"""Run update check from the page: the 'Source for this demo' selector, the banner result, and the no-baseline crash fix."""
import json

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import ROOT
from uhc_monitor import demo_data
from uhc_monitor.check import run_check
from uhc_monitor.demo_data import SCENARIOS, run_demo_check, seed_demo
from uhc_monitor.sources import Source, load_sources
from uhc_monitor.store import Store

APP = str(ROOT / "app.py")
BEDS = Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", None)
LABELS = ["Good PDF", "HTML error page (HTTP 200)", "HTML labelled as PDF", "Empty body (HTTP 200)", "Truncated file",
          "404 Not found", "500 Server error"]


def open_app():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def banners(at):
    return [x.value for group in (at.error, at.warning, at.success, at.info) for x in group if x.value.startswith("**")]


def run(at, scenario, policy="beds-mattresses"):
    at.selectbox(key=f"scenario-{policy}").select(scenario)
    next(b for b in at.button if b.label == "Run update check").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return banners(at)[0]


def test_a_first_check_that_failed_no_longer_crashes_the_page():
    s = Store(); assert run_check(s, BEDS).status == "failed"; s.db.close()          # no URL configured: state exists, no baseline
    st = Store().get_state("beds-mattresses"); assert st["last_status"] == "failed" and st["baseline_version_id"] is None
    at = open_app()
    banner = banners(at)[0]
    assert banner.startswith("**Beds and Mattresses: failed**") and "no URL configured" in banner
    assert "not 'unchanged'" in banner and "No check has succeeded yet, so there is nothing to compare" in banner
    assert "last good version is kept" not in banner                                  # there is none to keep
    assert [t.value for t in at.title] == ["Beds and Mattresses"]
    assert {b.label for b in at.button} >= {"Run update check", "Seed older version"}      # still usable from the page


def test_the_source_selector_lists_the_seven_test_server_scenarios():
    at = open_app()
    sel = at.selectbox(key="scenario-beds-mattresses")
    assert sel.label == "Source for this demo" and sel.options == LABELS and sel.value == "Good PDF"
    assert [label for label, _ in SCENARIOS] == LABELS


def test_changing_the_selector_alone_runs_no_check():
    at = open_app()
    at.selectbox(key="scenario-beds-mattresses").select("404 Not found").run()
    assert Store().get_state("beds-mattresses") is None and not Store().db.execute("SELECT COUNT(*) FROM checks").fetchone()[0]


@pytest.mark.ui
@pytest.mark.parametrize("scenario, reason", [
    ("HTML error page (HTTP 200)", "not a PDF"), ("HTML labelled as PDF", "not a PDF"), ("Empty body (HTTP 200)", "empty response"),
    ("Truncated file", "truncated"), ("404 Not found", "HTTP 404"), ("500 Server error", "HTTP 500")])
def test_each_bad_scenario_shows_failed_with_its_reason_and_the_page_keeps_working(scenario, reason):
    at = open_app()
    banner = run(at, scenario)
    assert banner.startswith("**Beds and Mattresses: failed**") and "Reason: " in banner and reason in banner
    assert "This is not 'unchanged'" in banner and "No check has succeeded yet" in banner
    assert Store().get_state("beds-mattresses")["baseline_version_id"] is None


def test_status_sequence_initialized_unchanged_failed_recovered():
    at = open_app()
    assert run(at, "Good PDF").startswith("**Beds and Mattresses: initialized**")
    assert run(at, "Good PDF").startswith("**Beds and Mattresses: unchanged**")
    failed = run(at, "500 Server error")
    assert failed.startswith("**Beds and Mattresses: failed**") and "HTTP 500" in failed and "The last good version is kept below." in failed
    store = Store(); good = store.get_state("beds-mattresses")["baseline_version_id"]
    assert good is not None and store.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 1       # nothing lost or duplicated
    assert run(at, "Good PDF").startswith("**Beds and Mattresses: unchanged**")
    assert Store().get_state("beds-mattresses")["baseline_version_id"] == good


@pytest.mark.ui
def test_seeding_works_after_a_failed_first_check_and_a_rerun_is_unchanged():
    at = open_app()
    run(at, "404 Not found")
    next(b for b in at.button if b.label == "Seed older version").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert banners(at)[0].startswith("**Beds and Mattresses: updated**") and "SIMULATED baseline" in banners(at)[0]
    assert [m.label for m in at.metric][:3] == ["Substantive", "Ambiguous — Needs human review", "Cosmetic"] and at.metric[0].value == "4"
    again = run(at, "Good PDF")
    assert again.startswith("**Beds and Mattresses: unchanged**")
    failed = run(at, "Empty body (HTTP 200)")                                         # a failure leaves the findings in place
    assert failed.startswith("**Beds and Mattresses: failed**") and "empty response" in failed
    assert at.metric[0].value == "4" and any(b.key and b.key.startswith("row-") for b in at.button)


@pytest.mark.ui
def test_the_knee_can_be_checked_from_the_page_through_its_own_route():
    at = open_app()
    at.selectbox(key=next(s.key for s in at.selectbox if s.label == "Policy")).select_index(1).run()
    assert [t.value for t in at.title] == ["Surgery of the Knee"]
    assert run(at, "Good PDF", "surgery-knee").startswith("**Surgery of the Knee: initialized**")
    assert [t.value for t in at.title] == ["Surgery of the Knee"] and any("2026T0553II" in m.value for m in at.markdown)
    assert run(at, "Good PDF", "surgery-knee").startswith("**Surgery of the Knee: unchanged**")
    assert "HTTP 404" in run(at, "404 Not found", "surgery-knee")
    s = Store()
    assert s.get_state("beds-mattresses") is None and s.get_state("surgery-knee")["baseline_version_id"]      # policies stay separate
    assert s.db.execute("SELECT policy_number FROM versions WHERE source_id='surgery-knee'").fetchone()[0] == "2026T0553II"


def test_real_urls_stay_null_and_the_page_says_so():
    assert all(s.url is None for s in load_sources())
    assert all(json.loads((ROOT / "config" / "sources.json").read_text())["sources"][i]["url"] is None for i in (0, 1))
    at = open_app()
    assert any("Real UHC URLs are not configured" in c.value and "local test server" in c.value for c in at.caption)
    run(at, "Good PDF")
    assert all(s.url is None for s in load_sources())                               # running a check never writes a URL anywhere


def test_the_demo_check_fetches_only_from_the_local_test_server(monkeypatch):
    seen = []
    real = demo_data.run_check
    monkeypatch.setattr(demo_data, "run_check", lambda store, source, **kw: (seen.append(source.url), real(store, source, **kw))[1])
    s = Store()
    run_demo_check(s, BEDS, "404 Not found")
    run_demo_check(s, Source("surgery-knee", "Surgery of the Knee", "UnitedHealthcare", None), "Truncated file")
    assert all(u.startswith("http://127.0.0.1:") for u in seen)
    assert seen[0].endswith("/missing") and seen[1].endswith("/surgery-knee/truncated.pdf")
    with pytest.raises(ValueError):
        run_demo_check(s, Source("other", "Other", "UnitedHealthcare", None), "Good PDF")


def test_seed_demo_is_allowed_after_a_failed_first_check_but_not_over_a_stored_version():
    s = Store(); run_check(s, BEDS)
    assert seed_demo(s, "beds-mattresses").status == "updated"
    assert seed_demo(s, "beds-mattresses") is None
