"""UHC policy change monitor: finding view. Run with: python -m streamlit run app.py"""
import streamlit as st

from uhc_monitor.classify import AMBIGUOUS, AMBIGUOUS_SUBTITLE, COSMETIC, SUBSTANTIVE
from uhc_monitor.demo_data import seed_demo
from uhc_monitor.findings import get_findings, latest_update
from uhc_monitor.sources import load_sources
from uhc_monitor.store import Store
from uhc_monitor.view import excerpt_pair

st.set_page_config(page_title="UHC Policy Monitor", layout="wide")


def badge(label, source="rules"):
    if label == SUBSTANTIVE:
        note = " · escalated by Claude (the rules said Ambiguous)" if source != "rules" else ""
        return f":red[**SUBSTANTIVE**]{note}"
    if label == AMBIGUOUS:
        return f":orange[**AMBIGUOUS**] · *{AMBIGUOUS_SUBTITLE}*"
    return ":gray[**COSMETIC**]"


def pages(f, before_sim):
    b = f"p.{f['before_page']}" if f["before_page"] else "not present"
    a = f"p.{f['after_page']}" if f["after_page"] else "not present"
    return f"before {b}{' (SIMULATED)' if before_sim else ''} → after {a}"


def render_excerpts(f, before_sim):
    b, a, full_b, full_a = excerpt_pair(f["before_text"], f["after_text"])
    left, right = st.columns(2)
    left.markdown(f"**Before** — {'SIMULATED older version' if before_sim else 'older version'}")
    left.markdown(b or "*(nothing: this text is new)*", unsafe_allow_html=True)
    right.markdown("**After** — current real document")
    right.markdown(a or "*(nothing: this text was removed)*", unsafe_allow_html=True)
    if full_b != b or full_a != a:
        with st.expander("Show full paragraphs"):
            left, right = st.columns(2)
            left.markdown(full_b or "*(nothing)*", unsafe_allow_html=True)
            right.markdown(full_a or "*(nothing)*", unsafe_allow_html=True)


def render_claude(llm):
    st.markdown("**Claude's suggestion** (advice only; it can raise a change to substantive but never dismiss one)")
    if llm is None:
        return
    status = llm["status"]
    if status == "skipped_no_key":
        st.caption("Claude was not asked: no API key is set, so this is rules only.")
    elif status != "ok":
        st.caption(f"Claude gave no usable answer ({status}"
                   f"{': ' + llm['detail'] if llm.get('detail') else ''}). The change stays as the rules left it.")
    else:
        ans = llm["answer"]
        st.markdown(f"Suggested label: **{ans['suggested_label']}**  \n"
                    f"Claude's self-reported confidence: **{ans['confidence']}**")
        st.markdown(f"What the two texts say (Claude's restatement): {ans['what_changed']}")


def render_finding(n, f, before_sim, facts):
    with st.container(border=True):
        st.markdown(f"#### {n}. {badge(f['final_label'], f['final_source'])}")
        st.markdown(f"**{f['path']}** · {pages(f, before_sim)}")

        st.markdown("##### Source facts")
        render_excerpts(f, before_sim)
        applies = "; ".join(f"{a['plan']} (p.{a['page']})" for a in facts["applies_to"]) or "Unknown"
        st.markdown(
            f"- Effective date (stated in the current document): **{facts['effective_date']}**\n"
            f"- Plans the document says it applies to (Application section): **{applies}**\n"
            f"- Population affected by this change: **Unknown** (not stated in the excerpt)")

        st.markdown("##### How it was classified")
        st.markdown(f"Rules: {badge(f['label'])}  \nMatched rule **{f['rule_id']}**: {f['rule']}  \n"
                    f"Evidence: {f['evidence']}")
        render_claude(f["llm"])

        st.markdown("##### Interpretation — not stated in the source")
        g = f["guidance"]
        ok = f["llm"] and f["llm"]["status"] == "ok"
        st.markdown(f"- **What it may mean (rule-based reading):** {g['explanation']}\n"
                    f"- **Suggested next step:** {g['next_step']}\n"
                    f"- **Uncertainty:** {g['uncertainty']}")
        if ok:
            ans = f["llm"]["answer"]
            st.markdown(f"- **Claude's interpretation:** {ans['interpretation']}\n"
                        f"- **Claude's suggested next step:** {ans['suggested_next_step']}\n"
                        f"- **Claude's stated uncertainty:** {ans['uncertainty']}")


store = Store()
source = load_sources()[0]
st.title("UHC policy change monitor")
st.caption(f"{source.payer} · {source.name}")

state = store.get_state(source.id)
if state is None:
    st.info("No versions stored yet. The real UHC URL is not configured, so you can load demo data: a SIMULATED "
            "older version as the baseline, then a check of the real PDF served by a local test server.")
    if st.button("Load demo data"):
        seed_demo(store, source.id)
        st.rerun()
    st.stop()

status = state["last_status"]
when = state["last_checked_at"]
reason = state["last_reason"]
if status == "failed":
    st.error(f"Last check FAILED ({when}): {reason}. This is not 'unchanged'. The last good version is kept below.")
elif status == "updated":
    st.warning(f"Updated ({when}): the document changed since the previous version.")
elif status == "unchanged":
    st.success(f"Unchanged ({when}): same content as the baseline.")
else:
    st.info(f"Initialized ({when}): baseline stored, nothing to compare yet.")

update = latest_update(store, source.id)
if update is None:
    st.write("No update to review yet.")
    st.stop()

with st.spinner("Preparing findings (asks Claude about ambiguous changes once, then caches)..."):
    data = get_findings(store, source.id, update["before_version_id"], update["version_id"])
before_v = store.get_version(data["before_version_id"])
after_v = store.get_version(data["after_version_id"])
before_sim = bool(before_v["simulated"])

st.subheader("Versions compared")
c1, c2 = st.columns(2)
c1.markdown(f"**Before:** v{before_v['id']} — " + ("**SIMULATED older version** (derived from the real text; not a UHC "
            "publication)" if before_sim else "real document") + f"  \nPolicy {before_v['policy_number']} · effective date: "
            f"{data['before_facts']['effective_date']}")
c2.markdown(f"**After:** v{after_v['id']} — real document, stored {after_v['created_at']}  \n"
            f"Policy {after_v['policy_number']} · effective date: {data['after_facts']['effective_date']}")

findings = data["findings"]
subst = [f for f in findings if f["final_label"] == SUBSTANTIVE]
amb = [f for f in findings if f["final_label"] == AMBIGUOUS]
cos = [f for f in findings if f["final_label"] == COSMETIC]
m1, m2, m3 = st.columns(3)
m1.metric("Substantive", len(subst))
m2.metric(f"Ambiguous — {AMBIGUOUS_SUBTITLE}", len(amb))
m3.metric("Cosmetic", len(cos))

cl = data["claude"]
st.caption(f"Claude fallback: {cl['model']}, prompt {cl['prompt_version']} — " +
           (f"asked about {cl['calls']} of {data['ambiguous_count']} ambiguous changes." if cl["available"]
            else "not run (no API key); rules only.") + " The Policy History table is excluded from comparison.")
for note in data["notes"]:
    st.caption(f"Note: {note}")

st.subheader("Findings to review")
n = 0
for f in subst + amb:
    n += 1
    render_finding(n, f, before_sim, data["after_facts"])
if not (subst or amb):
    st.write("No substantive or ambiguous findings.")

with st.expander(f"Cosmetic changes ({len(cos)}) — listed for transparency"):
    for f in cos:
        st.markdown(f"**{f['path']}** · {pages(f, before_sim)} · rule **{f['rule_id']}**: {f['rule']} ({f['evidence']})")
        b, a, _, _ = excerpt_pair(f["before_text"], f["after_text"])
        left, right = st.columns(2)
        left.markdown(b, unsafe_allow_html=True)
        right.markdown(a, unsafe_allow_html=True)
