"""Step 8 flow check (script, not yet pytest): review, restart, unchanged rerun, new revision.
Run: PYTHONPATH=. .venv/bin/python tests/flow_step8.py   Step 9 turns this into regression tests."""
import copy, json, os, tempfile
from pathlib import Path
os.environ["ANTHROPIC_API_KEY"] = ""
root = tempfile.mkdtemp(); os.environ["UHC_STORE_ROOT"] = root
from streamlit.testing.v1 import AppTest
from uhc_monitor import testserver
from uhc_monitor.check import run_check
from uhc_monitor.extract import extract_policy
from uhc_monitor.sources import Source
from uhc_monitor.store import Store

APP = str(Path(__file__).resolve().parent.parent / "app.py")
def open_app():                       # a fresh AppTest = a fresh session/restart (new Store, new connection)
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    return at
def metrics(at): return {m.label: m.value for m in at.metric}
def md(at): return "\n".join(m.value for m in at.markdown)
def rows(): 
    s = Store(root); r = s.db.execute("SELECT finding_key,status,note,reviewer_label,tool_label FROM reviews ORDER BY finding_key").fetchall(); s.db.close(); return r
def check(name, ok): print(("PASS " if ok else "FAIL ") + name); assert ok, name

at = open_app(); at.button[0].click().run()                       # seed demo
m = metrics(at); print("metrics after seed:", m)
check("fresh: 6 open, 0 reviewed, 0 dismissed", (m["Open"], m["Reviewed"], m["Dismissed"]) == ("6", "0", "0"))
check("'Not asked: the rules decided this one' shown", any("Not asked: the rules decided this one" in c.value for c in at.caption))

findings = json.load(open(f"{root}/findings/beds-mattresses_1_2.json"))["findings"]
order = [f for f in findings if f["final_label"] == "substantive"] + [f for f in findings if f["final_label"] == "ambiguous"]
keys = [f["key"] for f in order]
check("finding keys are unique", len(set(f["key"] for f in findings)) == len(findings))
amb_key = keys[4]; sub_key = keys[0]
btn = lambda at, i, which: at.button[2 * i + which]               # per finding: [Mark reviewed, Dismiss]
check("button labels", [b.label for b in at.button[:2]] == ["Mark reviewed", "Dismiss"])

# 1. ambiguous item: own label + note + reviewed
at.text_area(key=f"note-{amb_key}").input("wording only, no effect on coverage")
at.selectbox(key=f"label-{amb_key}").select("Cosmetic")
btn(at, 4, 0).click().run(); assert not at.exception, at.exception
r = {x["finding_key"]: x for x in rows()}
check("ambiguous reviewed with reviewer label + note saved", r[amb_key]["status"] == "reviewed" and r[amb_key]["reviewer_label"] == "cosmetic" and "wording only" in r[amb_key]["note"])
check("tool label recorded next to the reviewer label", r[amb_key]["tool_label"] == "ambiguous")
check("substantive item has no label picker", f"label-{sub_key}" not in [s.key for s in at.selectbox])
# 2. substantive item: dismissed with note
at.text_area(key=f"note-{sub_key}").input("already handled in the Q4 edit")
btn(at, 0, 1).click().run()
r = {x["finding_key"]: x for x in rows()}
check("substantive dismissed with note, no reviewer label", r[sub_key]["status"] == "dismissed" and r[sub_key]["reviewer_label"] is None)
m = metrics(at); check("counts: 4 open, 1 reviewed, 1 dismissed", (m["Open"], m["Reviewed"], m["Dismissed"]) == ("4", "1", "1"))
check("decided findings still on the page", "REVIEWED" in md(at) and "DISMISSED" in md(at) and len([h for h in at.markdown if h.value.startswith("####")]) >= 6)

# 3. restart: new session reads the saved decisions
at = open_app(); m = metrics(at)
check("after restart: decisions persisted", (m["Open"], m["Reviewed"], m["Dismissed"]) == ("4", "1", "1"))
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
decided = {x["finding_key"] for x in rows()}
display = [k for k in keys if k not in decided] + [k for k in keys if k in decided]   # open first, as the page shows them
btn(at, display.index(amb_key), 0).click().run()
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
