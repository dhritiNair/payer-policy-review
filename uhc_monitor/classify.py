"""Rules-based classifier: substantive, cosmetic or ambiguous, with the rule that decided it."""
from .diff import diff_documents_with_notes, word_changes

SUBSTANTIVE, COSMETIC, AMBIGUOUS = "substantive", "cosmetic", "ambiguous"
AMBIGUOUS_SUBTITLE = "Needs human review"   # shown wherever 'Ambiguous' is shown

# Sections (top-level headings) where a wording change can alter coverage or billing.
COVERAGE_SECTIONS = {"Application", "Coverage Rationale", "Medical Records Documentation Used for Reviews",
                     "Applicable Codes", "Benefit Considerations"}

# Words that decide whether, when or for whom something is covered.
COVERAGE_WORDS = {"all", "any", "both", "either", "only", "must", "shall", "should", "may", "not", "no", "never",
                  "non", "without", "except", "unless", "exclude", "excludes", "excluded", "exclusion",
                  "exclusions", "require", "requires", "required", "covered", "unproven", "experimental",
                  "investigational", "authorization"}

RULES = {
    "COS-TOC": (COSMETIC, "Only the table of contents changed (page numbers or entries); no policy text changed."),
    "COS-FORMAT": (COSMETIC, "Same words after ignoring case, spacing, punctuation and hyphenation."),
    "SUB-CODE-LIST": (SUBSTANTIVE, "A procedure code was added to or removed from the code list."),
    "SUB-PARAGRAPH": (SUBSTANTIVE, "A paragraph or criterion was added or removed in a coverage section."),
    "SUB-KEYWORD": (SUBSTANTIVE, "A coverage keyword (all/any/not/must/only...) was added, removed or swapped in a coverage section."),
    "SUB-NUMBER": (SUBSTANTIVE, "A number changed in a coverage section."),
    "AMB-MOVED": (AMBIGUOUS, "A paragraph moved to a different section; the wording is the same but its context changed."),
    "AMB-DEFAULT": (AMBIGUOUS, "No rule settled it: the wording changed in a way the rules cannot judge."),
}


def classify(change):
    """Return {label, rule_id, rule, evidence} for one change from diff_documents."""
    def decide(rule_id, evidence):
        label, rule = RULES[rule_id]
        return {"label": label, "rule_id": rule_id, "rule": rule, "evidence": evidence}

    if change["is_toc"]:
        return decide("COS-TOC", "change is inside the Table of Contents")

    in_coverage = change["h1"] in COVERAGE_SECTIONS
    kind = change["change_type"]

    if kind in ("code_added", "code_removed"):
        verb = "added" if kind == "code_added" else "removed"
        return decide("SUB-CODE-LIST", f"code {change['code']} {verb}") if in_coverage else \
            decide("AMB-DEFAULT", f"code {change['code']} {verb} outside a coverage section")

    if kind == "moved":
        return decide("AMB-MOVED", f"moved from '{change['from_path']}' to '{change['path']}'")

    if kind in ("added", "removed"):
        verb = "added" if kind == "added" else "removed"
        if in_coverage:
            return decide("SUB-PARAGRAPH", f"paragraph {verb} in '{change['path']}'")
        return decide("AMB-DEFAULT", f"paragraph {verb} outside the coverage sections ('{change['path']}')")

    removed, added = word_changes(change["before_text"], change["after_text"])
    changed = f"removed: {' '.join(removed) or '-'} | added: {' '.join(added) or '-'}"
    if not removed and not added:
        return decide("COS-FORMAT", "no word differences")
    if in_coverage:
        hits = sorted({w for w in removed + added if w in COVERAGE_WORDS})
        if hits:
            return decide("SUB-KEYWORD", f"coverage words {hits}; {changed}")
        if any(any(c.isdigit() for c in w) for w in removed + added):
            return decide("SUB-NUMBER", changed)
    where = "" if in_coverage else " (outside the coverage sections)"
    return decide("AMB-DEFAULT", changed + where)


def compare_with_notes(before, after):
    """(changes, notes): diff + classify. Each change comes back with its decision merged in."""
    changes, notes = diff_documents_with_notes(before, after)
    return [{**c, **classify(c)} for c in changes], notes


def compare(before, after):
    return compare_with_notes(before, after)[0]
