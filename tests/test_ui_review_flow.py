"""UI flow: saved reviews survive a restart and an unchanged rerun; a new revision opens for review again."""
import copy
import json
from pathlib import Path

import copy, json
from streamlit.testing.v1 import AppTest
from uhc_monitor import testserver
from uhc_monitor.check import run_check
from uhc_monitor.extract import extract_policy
from uhc_monitor.sources import Source
from uhc_monitor.store import Store
import pytest
from tests.helpers import check



@pytest.mark.ui
def test_review_flow_persists_across_restart_rerun_and_new_revision(app_env):
    root = app_env
    APP = str(Path(__file__).resolve().parent.parent / "app.py")
    def open_app():                       # a fresh AppTest = a fresh session/restart (new Store, new connection)
        at = AppTest.from_file(APP, default_timeout=60).run()
        assert not at.exception, at.exception
        return at
    def metrics(at): return {m.label: m.value for m in at.metric}
    def md(at): return "\n".join(m.value for m in at.markdown)
    def rows(): 
        s = Store(root); r = s.db.execute("SELECT finding_key,status,note,reviewer_label,tool_label FROM reviews ORDER BY finding_key").fetchall(); s.db.close(); return r

    at = open_app(); next(b for b in at.button if b.label == "Load demo data").click().run()                       # seed demo
    m = metrics(at); print("metrics after seed:", m)
    check("fresh: 6 open, 0 reviewed, 0 dismissed", (m["Open"], m["Reviewed"], m["Dismissed"]) == ("6", "0", "0"))

    check("summary: two rows of four numbers, in order",
          [m.label for m in at.metric] == ["Substantive", "Ambiguous — Needs human review", "Cosmetic", "Resolved by reviewer",
                                           "Open", "Reviewed", "Dismissed", "Total findings"])
    check("summary: Total findings = Open + Reviewed + Dismissed", int(m["Total findings"]) == int(m["Open"]) + int(m["Reviewed"]) + int(m["Dismissed"]))
    check("summary rows are labelled", [x.value for x in at.markdown if x.value in ("**By type**", "**Review progress**")] == ["**By type**", "**Review progress**"])
    vers = [x.value for x in at.markdown if x.value.startswith(("**Older", "**Current"))]
    check("two short version lines, no repeat of what the header says",
          len(vers) == 2 and vers[0].startswith("**Older (simulated):** v1") and vers[1].startswith("**Current (real):** v2")
          and "MP.028.14" not in "".join(vers) and "2026-01-01" not in "".join(vers) and "T0" not in "".join(vers))
    check("old 'Versions compared' heading is gone", "Versions compared" not in [x.value for x in at.subheader])
    findings = json.load(open(f"{root}/findings/beds-mattresses_1_2.json"))["findings"]
    order = [f for f in findings if f["final_label"] == "substantive"] + [f for f in findings if f["final_label"] == "ambiguous"]
    keys = [f["key"] for f in order]
    check("finding keys are unique", len(set(f["key"] for f in findings)) == len(findings))
    amb_key = keys[4]; sub_key = keys[0]
    def open_row(at, key):                # click a finding row; its detail (and review form) opens below the columns
        rows = [b for b in at.button if b.key in (f"row-{key}", f"rowsel-{key}")]
        if rows[0].key.startswith("row-"):
            rows[0].click().run()
        assert not at.exception, at.exception
    def form_btn(at, label): return [b for b in at.button if b.label == label][0]
    check("no detail open yet: no review form shown", not [b for b in at.button if b.label == "Mark reviewed"])
    open_row(at, sub_key)
    check("'Not asked: the rules decided this one' shown for a rule-decided finding", any("Not asked: the rules decided this one" in c.value for c in at.caption))
    open_row(at, amb_key)
    check("one finding open: one review form", len([b for b in at.button if b.label == "Mark reviewed"]) == 1)

    # 1. ambiguous item: own label + note + reviewed
    at.text_area(key=f"note-{amb_key}").input("wording only, no effect on coverage")
    at.selectbox(key=f"label-{amb_key}").select("Cosmetic")
    form_btn(at, "Mark reviewed").click().run(); assert not at.exception, at.exception
    r = {x["finding_key"]: x for x in rows()}
    check("ambiguous reviewed with reviewer label + note saved", r[amb_key]["status"] == "reviewed" and r[amb_key]["reviewer_label"] == "cosmetic" and "wording only" in r[amb_key]["note"])
    check("tool label recorded next to the reviewer label", r[amb_key]["tool_label"] == "ambiguous")
    m = metrics(at)
    check("reviewer's Cosmetic label takes effect: Substantive 4, Ambiguous 1, Cosmetic 5, Resolved by reviewer 1",
          (m["Substantive"], m["Ambiguous — Needs human review"], m["Cosmetic"], m["Resolved by reviewer"]) == ("4", "1", "5", "1"))
    check("substantive item has no label picker", f"label-{sub_key}" not in [s.key for s in at.selectbox])
    # 2. substantive item: dismissed with note (opening another row replaces the first detail: one at a time)
    open_row(at, sub_key)
    check("opening a second finding closes the first (one review form)", len([b for b in at.button if b.label == "Mark reviewed"]) == 1
          and f"label-{amb_key}" not in [s.key for s in at.selectbox])
    at.text_area(key=f"note-{sub_key}").input("already handled in the Q4 edit")
    form_btn(at, "Dismiss").click().run()
    r = {x["finding_key"]: x for x in rows()}
    check("substantive dismissed with note, no reviewer label", r[sub_key]["status"] == "dismissed" and r[sub_key]["reviewer_label"] is None)
    m = metrics(at); check("counts: 4 open, 1 reviewed, 1 dismissed", (m["Open"], m["Reviewed"], m["Dismissed"]) == ("4", "1", "1"))
    check("decided findings still listed as rows, marked", any("REVIEWED" in b.label for b in at.button) and any("DISMISSED" in b.label for b in at.button)
          and len([b for b in at.button if b.key and b.key.startswith(("row-", "rowsel-"))]) == 6)

    # 3. restart: new session reads the saved decisions
    at = open_app(); m = metrics(at)
    check("after restart: decisions persisted", (m["Open"], m["Reviewed"], m["Dismissed"]) == ("4", "1", "1"))
    open_row(at, amb_key)
    check("after restart: the reviewer's label still applies", (m["Ambiguous — Needs human review"], m["Cosmetic"], m["Resolved by reviewer"]) == ("1", "5", "1"))
    check("after restart: note prefilled", at.text_area(key=f"note-{amb_key}").value.startswith("wording only"))
    check("ambiguous still labelled Needs human review until decided", "Needs human review" in md(at))

    # 4. unchanged rerun
    server, base = testserver.start(); src = Source("beds-mattresses", "Beds and Mattresses", "UnitedHealthcare", f"{base}/ok.pdf")
    store = Store(root); n_versions = store.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    o = run_check(store, src); check("rerun status is unchanged", o.status == "unchanged")
    check("no new version, no new update", store.db.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == n_versions
          and store.db.execute("SELECT COUNT(*) FROM checks WHERE status='updated'").fetchone()[0] == 1)
    at = open_app(); m = metrics(at)
    check("after unchanged rerun: same decisions, same 6 findings", (m["Open"], m["Reviewed"], m["Dismissed"]) == ("4", "1", "1"))
    open_row(at, amb_key)
    form_btn(at, "Mark reviewed").click().run()
    check("saving the same review twice keeps one row and the same decision",
          len(rows()) == 2 and {x["finding_key"]: x["status"] for x in rows()} == {amb_key: "reviewed", sub_key: "dismissed"})

    # 5. genuinely new revision: the Benefit Considerations sentence changes again
    def next_revision(data):
        d = copy.deepcopy(extract_policy(data))
        for s in d["sections"]:
            if s["heading"] == "Benefit Considerations":
                s["paragraphs"][0]["text"] = s["paragraphs"][0]["text"].replace("Most benefit plans", "Several benefit plans")
        return d
    o = run_check(store, src, extract=next_revision); check("new revision reported as updated", o.status == "updated")
    store.db.close()
    at = open_app(); m = metrics(at)
    print("metrics for the new update:", m)
    check("new revision opens for review again (all open, none carried over)", m["Open"] == str(int(m["Substantive"]) + int(m["Ambiguous — Needs human review"])) and m["Reviewed"] == "0" and m["Dismissed"] == "0")
    sel = [s for s in at.selectbox if s.label.startswith("Update to review")][0]
    check("update selector offers both updates", len(sel.options) == 2)
    sel.select_index(1).run(); m = metrics(at); pass
    check("older update still shows its saved decisions", (m["Reviewed"], m["Dismissed"]) == ("1", "1"))
    check("review rows untouched by the new revision: 2", len(rows()) == 2)
    server.shutdown(); print("\nall checks passed")
