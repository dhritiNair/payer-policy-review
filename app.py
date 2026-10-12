"""UHC policy change monitor: finding view. Run with: python -m streamlit run app.py"""
from datetime import datetime

import streamlit as st

from uhc_monitor.classify import AMBIGUOUS, AMBIGUOUS_SUBTITLE, COSMETIC, SUBSTANTIVE
from uhc_monitor.demo_data import seed_demo
from uhc_monitor.findings import all_updates, get_findings, policy_facts
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
        st.caption("Not asked: the rules decided this one.")
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


LABEL_CHOICES = {"Still unsure": "unsure", "Substantive": "substantive", "Cosmetic": "cosmetic"}
LABEL_NAMES = {v: k for k, v in LABEL_CHOICES.items()}


def review_badge(review):
    if review is None:
        return "· **OPEN** (not yet reviewed)"
    mark = ":green[**REVIEWED**]" if review["status"] == "reviewed" else ":gray[**DISMISSED**]"
    return f"· {mark}"


def render_review(store, f, review, ctx):
    st.markdown("##### Your review")
    if review:
        label = f" · your label: **{LABEL_NAMES[review['reviewer_label']]}**" if review["reviewer_label"] else ""
        st.markdown(f"Saved as **{review['status']}** on {review['updated_at']}{label}" +
                    (f"  \nNote: {review['note']}" if review["note"] else ""))
    with st.form(f"form-{f['key']}"):
        note = st.text_area("Note", value=review["note"] if review else "", key=f"note-{f['key']}")
        label = None
        if f["label"] == AMBIGUOUS:   # only changes the rules could not decide get a reviewer label
            names = list(LABEL_CHOICES)
            current = LABEL_NAMES.get(review["reviewer_label"], "Still unsure") if review else "Still unsure"
            label = LABEL_CHOICES[st.selectbox("Your label", names, index=names.index(current),
                                               key=f"label-{f['key']}")]
        left, right = st.columns(2)
        reviewed = left.form_submit_button("Mark reviewed")
        dismissed = right.form_submit_button("Dismiss")
    if reviewed or dismissed:
        store.save_review(f["key"], ctx["source_id"], ctx["before_id"], ctx["after_id"],
                          "reviewed" if reviewed else "dismissed", note, label, f["final_label"])
        st.rerun()


def render_finding(n, f, before_sim, facts, review, store, ctx):
    with st.container(border=True):
        st.markdown(f"#### {n}. {badge(f['final_label'], f['final_source'])} {review_badge(review)}")
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
        render_review(store, f, review, ctx)


def fmt_time(iso):
    return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M UTC")


def render_header(slot, source, facts, state, compared):
    """Policy header: what policy is under review, which versions, and when it was last checked."""
    with slot:
        st.caption("UHC policy change monitor")
        st.title(facts["title"] if facts else source.name)
        st.markdown(f"**{(facts and facts['product_line']) or source.payer}**")
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**Policy number**  \n{facts['policy_number'] if facts else 'Unknown'}")
        c2.markdown(f"**Effective date (current)**  \n{facts['effective_date'] if facts else 'Unknown'}")
        c3.markdown(f"**Version compared**  \n{compared}")
        c4.markdown(f"**Last checked**  \n{fmt_time(state['last_checked_at']) if state else 'Never'}")


def render_banner(slot, name, state):
    with slot:
        status, when, reason = state["last_status"], fmt_time(state["last_checked_at"]), state["last_reason"]
        if status == "failed":
            st.error(f"**{name}: failed** — last check at {when}: {reason}. This is not 'unchanged'. "
                     "The last good version is kept below.")
        elif status == "updated":
            st.warning(f"**{name}: updated** — the document changed since the previous version (checked {when}).")
        elif status == "unchanged":
            st.success(f"**{name}: unchanged** — same content as the baseline (checked {when}).")
        else:
            st.info(f"**{name}: initialized** — baseline stored, nothing to compare yet (checked {when}).")


store = Store()
sources = load_sources()
selector_slot, header_slot, banner_slot = st.container(), st.container(), st.container()
if len(sources) > 1:
    with selector_slot:
        source = sources[st.selectbox("Policy", range(len(sources)), format_func=lambda i: sources[i].name)]
else:
    source = sources[0]

state = store.get_state(source.id)
if state is None:
    render_header(header_slot, source, None, None, "none yet")
    st.info("No versions stored yet. The real UHC URL is not configured, so you can load demo data: a SIMULATED "
            "older version as the baseline, then a check of the real PDF served by a local test server.")
    if st.button("Load demo data"):
        seed_demo(store, source.id)
        st.rerun()
    st.stop()

current_v = store.get_version(state["baseline_version_id"])
facts = policy_facts(store.load_extracted(current_v["id"]), bool(current_v["simulated"]))
policy_name = facts["title"] or source.name
render_banner(banner_slot, policy_name, state)

updates = all_updates(store, source.id)
if not updates:
    render_header(header_slot, source, facts, state,
                  f"v{current_v['id']} {'SIMULATED' if current_v['simulated'] else 'real'} (nothing to compare yet)")
    st.write("No update to review yet.")
    st.stop()
if len(updates) > 1:
    names = [f"v{u['before_version_id']} → v{u['version_id']} · checked {fmt_time(u['checked_at'])}" for u in updates]
    update = updates[st.selectbox("Update to review (newest first)", range(len(updates)),
                                  format_func=lambda i: names[i])]
else:
    update = updates[0]

with st.spinner("Preparing findings (asks Claude about ambiguous changes once, then caches)..."):
    data = get_findings(store, source.id, update["before_version_id"], update["version_id"])
before_v = store.get_version(data["before_version_id"])
after_v = store.get_version(data["after_version_id"])
before_sim = bool(before_v["simulated"])
ctx = {"source_id": source.id, "before_id": data["before_version_id"], "after_id": data["after_version_id"]}
tag = lambda v: "SIMULATED" if v["simulated"] else "real"
render_header(header_slot, source, facts, state,
              f"v{before_v['id']} {tag(before_v)} → v{after_v['id']} {tag(after_v)}")

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

to_review = subst + amb
reviews = store.get_reviews(f["key"] for f in to_review)
n_open = sum(f["key"] not in reviews for f in to_review)
n_rev = sum(r["status"] == "reviewed" for r in reviews.values())
n_dis = sum(r["status"] == "dismissed" for r in reviews.values())
r1, r2, r3 = st.columns(3)
r1.metric("Open", n_open)
r2.metric("Reviewed", n_rev)
r3.metric("Dismissed", n_dis)

cl = data["claude"]
st.caption(f"Claude fallback: {cl['model']}, prompt {cl['prompt_version']} — " +
           (f"asked about {cl['calls']} of {data['ambiguous_count']} ambiguous changes." if cl["available"]
            else "not run (no API key); rules only.") + " The Policy History table is excluded from comparison.")
for note in data["notes"]:
    st.caption(f"Note: {note}")

st.subheader("Findings to review")
# open findings first; reviewed and dismissed ones stay on the page, marked, so nothing disappears
ordered = [f for f in to_review if f["key"] not in reviews] + [f for f in to_review if f["key"] in reviews]
for n, f in enumerate(ordered, 1):
    render_finding(n, f, before_sim, data["after_facts"], reviews.get(f["key"]), store, ctx)
if not ordered:
    st.write("No substantive or ambiguous findings.")

with st.expander(f"Cosmetic changes ({len(cos)}) — listed for transparency"):
    for f in cos:
        st.markdown(f"**{f['path']}** · {pages(f, before_sim)} · rule **{f['rule_id']}**: {f['rule']} ({f['evidence']})")
        b, a, _, _ = excerpt_pair(f["before_text"], f["after_text"])
        left, right = st.columns(2)
        left.markdown(b, unsafe_allow_html=True)
        right.markdown(a, unsafe_allow_html=True)
