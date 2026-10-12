"""UHC policy change monitor: finding view. Run with: python -m streamlit run app.py"""
from datetime import datetime

import streamlit as st

from uhc_monitor.classify import AMBIGUOUS, AMBIGUOUS_SUBTITLE, COSMETIC, SUBSTANTIVE
from uhc_monitor.demo_data import SCENARIOS, has_demo, run_demo_check, seed_demo
from uhc_monitor.findings import all_updates, get_findings, policy_facts
from uhc_monitor.sources import load_sources
from uhc_monitor.store import Store
from uhc_monitor.view import changed_summary, excerpt_pair

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


def reviewer_resolution(f, review):
    """SUBSTANTIVE or COSMETIC when a reviewer settled a change the rules left ambiguous, else None.
    'Still unsure' (or no review) leaves the finding where the tool put it, so this is always reversible."""
    if review and f["label"] == AMBIGUOUS and review["reviewer_label"] in (SUBSTANTIVE, COSMETIC):
        return review["reviewer_label"]
    return None


def resolution_text(resolution):
    to = ":red[**SUBSTANTIVE**]" if resolution == SUBSTANTIVE else ":gray[**COSMETIC**]"
    return f":orange[**AMBIGUOUS**] → {to} (reviewer)"


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
            label = LABEL_CHOICES[st.selectbox(
                "Your label", names, index=names.index(current), key=f"label-{f['key']}",
                help="Substantive or Cosmetic moves this finding to that group, marked reviewer-resolved. "
                     "Still unsure keeps it where the tool put it.")]
        left, right = st.columns(2)
        reviewed = left.form_submit_button("Mark reviewed")
        dismissed = right.form_submit_button("Dismiss")
    if reviewed or dismissed:
        store.save_review(f["key"], ctx["source_id"], ctx["before_id"], ctx["after_id"],
                          "reviewed" if reviewed else "dismissed", note, label, f["final_label"])
        st.rerun()


def render_finding(n, f, before_sim, facts, review, store, ctx, resolution=None):
    with st.container(border=True):
        st.markdown(f"#### {f'P{n} · ' if n else ''}{badge(f['final_label'], f['final_source'])} {review_badge(review)}")
        if resolution:
            st.markdown(f"**Reviewer-resolved: {resolution_text(resolution)}.** The tool's label above and its "
                        "classification below are kept. To reverse this, set \"Your label\" to Still unsure and save.")
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
        why = f" Reason: {reason}." if reason else ""
        if status == "failed":
            kept = ("The last good version is kept below." if state["baseline_version_id"]
                    else "No check has succeeded yet, so there is nothing to compare.")
            st.error(f"**{name}: failed** — last check at {when}.{why} This is not 'unchanged'. {kept}")
        elif status == "updated":
            st.warning(f"**{name}: updated** — the document changed since the previous version (checked {when}).{why}")
        elif status == "unchanged":
            st.success(f"**{name}: unchanged** — same content as the baseline (checked {when}).{why}")
        else:
            st.info(f"**{name}: initialized** — baseline stored, nothing to compare yet (checked {when}).{why}")


def render_check_panel(slot, source):
    """Run the real update check for this policy against the local test server; returns True if a check just ran."""
    ran = False
    with slot:
        with st.container(border=True):
            st.markdown("**Run update check**")
            left, right = st.columns([3, 1])
            scenario = left.selectbox("Source for this demo", [label for label, _ in SCENARIOS], key=f"scenario-{source.id}",
                                      help="What the local test server answers with. The check itself is the real one: it "
                                           "validates the download, compares it with the stored baseline and sets the status.")
            right.markdown("&nbsp;", unsafe_allow_html=True)
            if right.button("Run update check", key=f"run-check-{source.id}", use_container_width=True):
                run_demo_check(store, source, scenario)
                ran = True
            st.caption("Real UHC URLs are not configured (`url` is null in config/sources.json), so this demo checks a local "
                       "test server instead of uhcprovider.com. The result appears in the banner above.")
    return ran


store = Store()
sources = load_sources()
selector_slot, header_slot, banner_slot, check_slot = st.container(), st.container(), st.container(), st.container()
if len(sources) > 1:
    with selector_slot:
        source = sources[st.selectbox("Policy", range(len(sources)), format_func=lambda i: sources[i].name)]
else:
    source = sources[0]

render_check_panel(check_slot, source)      # a click runs the check now, so the state read below already reflects it
state = store.get_state(source.id)
if state is None or not state["baseline_version_id"]:
    # nothing good stored yet: either no check has run, or the first check failed
    render_header(header_slot, source, None, state, "none yet")
    if state:
        render_banner(banner_slot, source.name, state)
    if has_demo(source.id):
        st.info("No versions stored yet. The real UHC URL is not configured, so you can load demo data: a SIMULATED "
                "older version as the baseline, then a check of the real PDF served by a local test server.")
        if st.button("Load demo data"):
            seed_demo(store, source.id)
            st.rerun()
    else:
        st.info("No versions stored yet. The real UHC URL is not configured, and there is no demo data for this policy yet.")
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

if before_sim:
    st.markdown(f"**Older (simulated):** v{before_v['id']} · derived from the real text, not a UHC publication · "
                f"created {fmt_time(before_v['created_at'])} · effective date unknown")
else:
    st.markdown(f"**Older (real):** v{before_v['id']} · stored {fmt_time(before_v['created_at'])}")
st.markdown(f"**Current (real):** v{after_v['id']} · stored {fmt_time(after_v['created_at'])}")

findings = data["findings"]
tool_subst = [f for f in findings if f["final_label"] == SUBSTANTIVE]
tool_amb = [f for f in findings if f["final_label"] == AMBIGUOUS]
tool_cos = [f for f in findings if f["final_label"] == COSMETIC]
to_review = tool_subst + tool_amb   # what the tool put up for review; a reviewer can settle some of these
reviews = store.get_reviews(f["key"] for f in to_review)
resolution = {f["key"]: reviewer_resolution(f, reviews.get(f["key"])) for f in to_review}
res_sub = [f for f in to_review if resolution[f["key"]] == SUBSTANTIVE]
res_cos = [f for f in to_review if resolution[f["key"]] == COSMETIC]
# where each finding is shown now: a reviewer's Substantive/Cosmetic label overrides the tool's group
show_subst = [f for f in tool_subst if resolution[f["key"]] != COSMETIC] + [f for f in tool_amb if resolution[f["key"]] == SUBSTANTIVE]
show_amb = [f for f in tool_amb if resolution[f["key"]] is None]
n_cos = len(tool_cos) + len(res_cos)

n_open = sum(f["key"] not in reviews for f in to_review)
n_rev = sum(r["status"] == "reviewed" for r in reviews.values())
n_dis = sum(r["status"] == "dismissed" for r in reviews.values())

st.markdown("**By type**")
m1, m2, m3, m4 = st.columns(4)
m1.metric("Substantive", len(show_subst))
m2.metric(f"Ambiguous — {AMBIGUOUS_SUBTITLE}", len(show_amb))
m3.metric("Cosmetic", n_cos)
m4.metric("Resolved by reviewer", len(res_sub) + len(res_cos),
          help=f"{len(res_sub)} → Substantive, {len(res_cos)} → Cosmetic. The tool's own label stays on each finding.")

st.markdown("**Review progress**")
r1, r2, r3, r4 = st.columns(4)
r1.metric("Open", n_open)
r2.metric("Reviewed", n_rev)
r3.metric("Dismissed", n_dis)
r4.metric("Total findings", len(to_review),
          help="Findings put up for review (substantive and ambiguous). Cosmetic changes need no review.")

cl = data["claude"]
st.caption(f"Claude fallback: {cl['model']}, prompt {cl['prompt_version']} — " +
           (f"asked about {cl['calls']} of {data['ambiguous_count']} ambiguous changes." if cl["available"]
            else "not run (no API key); rules only.") + " The Policy History table is excluded from comparison.")
for note in data["notes"]:
    st.caption(f"Note: {note}")

ROW_CSS = """<style>
/* finding rows: left-aligned, wrap instead of truncating (the status must stay visible) */
[class*="st-key-row"] button { justify-content: flex-start; text-align: left; height: auto; min-height: 2.6rem;
    padding: 0.5rem 0.75rem; white-space: normal; }
[class*="st-key-row"] button > div, [class*="st-key-row"] button > div > span {
    justify-content: flex-start; width: 100%; text-align: left; }
[class*="st-key-row"] button [data-testid="stMarkdownContainer"] { width: 100%; text-align: left; }
[class*="st-key-row"] button p { display: block; text-align: left; margin: 0; white-space: normal; overflow: visible;
    text-overflow: clip; overflow-wrap: anywhere; }
[class*="st-key-rowsel-"] button { border: 2px solid rgba(128,128,128,0.95); background: rgba(128,128,128,0.18); }
</style>"""


def ambiguous_tier(f):
    """0 = abstained, low confidence, or no usable Claude answer (the reviewer has least to lean on); 1 medium; 2 high."""
    llm = f["llm"]
    if not llm or llm["status"] != "ok":
        return 0
    ans = llm["answer"]
    if ans["suggested_label"] == "abstain" or ans["confidence"] == "low":
        return 0
    return 1 if ans["confidence"] == "medium" else 2


def select_row(key):
    st.session_state["selected"] = None if st.session_state.get("selected") == key else key


def close_detail():
    st.session_state["selected"] = None


def row_label(f, prio, review, selected, resolved=None):
    arrow = "▼" if selected else "▶"
    if resolved:
        label = f"{resolution_text(resolved)} · *Reviewer-resolved*"
    elif f["final_label"] == SUBSTANTIVE:
        label = ":red[**SUBSTANTIVE**]" + (" (raised by Claude)" if f["final_source"] != "rules" else "")
    else:
        label = f":orange[**AMBIGUOUS**] *({AMBIGUOUS_SUBTITLE})*"
    page = f"p.{f['after_page']}" if f["after_page"] else f"p.{f['before_page']} (removed)"
    status = "OPEN" if review is None else review["status"].upper()
    num = f"**P{prio}** · " if prio else ""
    return f"{arrow} {num}{label} · {f['path']}, {page}\n\n`{changed_summary(f)}` · **{status}**"


def finding_row(f, prio, selected):
    is_sel = f["key"] == selected
    st.button(row_label(f, prio, reviews.get(f["key"]), is_sel, resolution.get(f["key"])), use_container_width=True,
              key=f"{'rowsel' if is_sel else 'row'}-{f['key']}", on_click=select_row, args=(f["key"],))


st.subheader("Findings to review")
st.markdown(ROW_CSS, unsafe_allow_html=True)
order = {f["key"]: i for i, f in enumerate(findings)}
# priority numbers come from the tool's own groups (including findings a reviewer has since moved), so they never shift;
# findings a reviewer moves INTO the Substantive column are numbered after the tool's substantive ones
rank_sub = sorted(tool_subst, key=lambda f: (f["final_source"] == "rules", order[f["key"]]))
rank_amb = sorted(tool_amb, key=lambda f: (ambiguous_tier(f), order[f["key"]]))
prio = {f["key"]: n for col in (rank_sub, rank_amb) for n, f in enumerate(col, 1)}
for n, f in enumerate([f for f in res_sub if f["final_label"] != SUBSTANTIVE], len(rank_sub) + 1):
    prio[f["key"]] = n
selected = st.session_state.get("selected")
col_s, col_a = st.columns(2)
for col, name, items in ((col_s, f"Substantive ({len(show_subst)})", show_subst),
                         (col_a, f"Ambiguous — {AMBIGUOUS_SUBTITLE} ({len(show_amb)})", show_amb)):
    with col:
        st.markdown(f"#### {name}")
        # open before decided, each in priority order; decided findings stay listed, marked, so nothing disappears
        items = sorted(items, key=lambda f: (f["key"] in reviews, prio[f["key"]]))
        for f in items:
            finding_row(f, prio[f["key"]], selected)
        if not items:
            st.caption("None.")

chosen = next((f for f in to_review if f["key"] == selected), None)
if chosen:
    st.divider()
    head, close = st.columns([6, 1])
    head.subheader("Finding detail")
    close.button("Close", key="close-detail", on_click=close_detail, use_container_width=True)
    render_finding(prio.get(chosen["key"]) if resolution[chosen["key"]] != COSMETIC else None, chosen, before_sim,
                   data["after_facts"], reviews.get(chosen["key"]), store, ctx, resolution[chosen["key"]])
else:
    st.caption("Select a finding above to see its full detail and review it.")

with st.expander(f"Cosmetic changes ({n_cos})" + (f" — {len(res_cos)} reviewer-resolved" if res_cos else "") +
                 " — listed for transparency", expanded=selected in {f["key"] for f in res_cos}):
    if res_cos:
        st.markdown("**Reviewer-resolved** (click a row to reopen its review; the tool labelled these Ambiguous)")
        for f in res_cos:
            finding_row(f, None, selected)
        st.markdown("**Cosmetic by the rules**")
    for f in tool_cos:
        st.markdown(f"**{f['path']}** · {pages(f, before_sim)} · rule **{f['rule_id']}**: {f['rule']} ({f['evidence']})")
        b, a, _, _ = excerpt_pair(f["before_text"], f["after_text"])
        left, right = st.columns(2)
        left.markdown(b, unsafe_allow_html=True)
        right.markdown(a, unsafe_allow_html=True)
