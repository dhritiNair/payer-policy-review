"""Extract text and structure (page and section mapping) from a UHC policy PDF."""
import json
import re
import sys
from datetime import datetime

import pymupdf

FOOTER_Y = 730        # header/footer block sits below this y on every page
LEVEL1_SIZE = 13.5    # Georgia-Bold 14pt: major sections
LEVEL2_SIZE = 11.5    # Georgia-Bold 11.9pt: subsections
SIDEBAR_X = (320, 400)  # front-page "Related Policy" sidebar: kept as its own section
ARROW = "\uf0dc"
LINE_GAP = 13         # y-gap above this starts a new paragraph


def _lines(page):
    """Visual lines on a page: spans merged by y position, ordered top to bottom."""
    rows = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if spans:
                rows.append((line["bbox"][1], line["bbox"][0], line["bbox"][2], spans))
    rows.sort()
    merged = []
    for y, x0, x1, spans in rows:
        # same visual row only if horizontally adjacent (bullet + text, code + description);
        # far-apart pieces are separate columns and stay separate
        last = next((m for m in reversed(merged) if abs(y - m["y"]) <= 3 and 0 <= x0 - m["x1"] < 60
                     and re.fullmatch(r"•|o|[A-Z]\d{4}|Policy Number|Effective Date|HCPCS Code",
                                      " ".join(sp["text"].strip() for sp in m["spans"]).strip(": ")) is not None), None)
        if last:
            last["spans"] += spans
            last["x1"] = x1
        else:
            merged.append({"y": y, "x0": x0, "x1": x1, "spans": list(spans)})
    merged.sort(key=lambda m: m["y"])
    out = []
    for m in merged:
        if m["y"] >= FOOTER_Y:
            continue
        spans = sorted(m["spans"], key=lambda s: s["bbox"][0])
        # a Wingdings arrow marks a cross-reference link; drop it and keep what follows as its own line
        segments = [[]]
        for sp in spans:
            if ARROW in sp["text"]:
                segments.append([])
            else:
                segments[-1].append(sp)
        for i, seg in enumerate(segments):
            if not seg:
                continue
            text = " ".join(sp["text"].strip() for sp in seg)
            text = re.sub(r"\s+([®:.,;)])", r"\1", text)
            text = re.sub(r"\.{4,}", " ... ", text)
            out.append({"sidebar": SIDEBAR_X[0] <= m["x0"] < SIDEBAR_X[1], "y": m["y"], "text": text.strip(),
                        "size": max(sp["size"] for sp in seg), "heading_font": "Georgia-Bold" in seg[0]["font"],
                        "break_before": i > 0})
    return out


def _parse_date(text):
    for fmt in ("%B %d, %Y", "%m/%d/%Y", "%m.%d.%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _join_across_pages(paras):
    """A paragraph that carries on at the top of the next page (lowercase start) is one paragraph."""
    out = []
    for p in paras:
        if out and p["page"] > out[-1]["page_end"] and p["text"][:1].islower():
            out[-1]["text"] += " " + p["text"]
            out[-1]["page_end"] = p["page"]
        else:
            out.append(p)
    return out


def _split_code_table(section):
    """Move 'E0193 description' rows into section['code_table'] as structured rows."""
    rows, rest = [], []
    for p in section["paragraphs"]:
        m = re.match(r"^([A-Z]\d{4})\s+(.+)$", p["text"])
        if m:
            rows.append({"code": m.group(1), "description": m.group(2), "page": p["page"]})
        elif re.fullmatch(r"HCPCS Code\s+Description", p["text"]):
            continue   # repeated column header
        else:
            rest.append(p)
    section["paragraphs"] = rest
    if rows:
        section["code_table"] = rows


def extract_policy(pdf):
    """pdf is a file path or the PDF's bytes."""
    doc = pymupdf.open(stream=pdf, filetype="pdf") if isinstance(pdf, bytes) else pymupdf.open(pdf)
    sections = [{"heading": "(Front matter)", "level": 0, "page_start": 1, "page_end": 1, "paragraphs": []}]
    sidebar = {"heading": "(Related policies sidebar)", "level": 0, "page_start": 1, "page_end": 1, "paragraphs": []}
    section = sections[0]
    seen_level1 = False
    title = None

    for pno, page in enumerate(doc, start=1):
        prev = None
        para = None
        for ln in _lines(page):
            if ln["sidebar"] and pno == 1:
                sidebar["paragraphs"].append({"page": pno, "text": ln["text"], "last_y": ln["y"], "size": ln["size"]})
                continue
            is_h1 = ln["heading_font"] and ln["size"] >= LEVEL1_SIZE and ln["size"] < 18
            is_h2 = seen_level1 and ln["heading_font"] and LEVEL2_SIZE <= ln["size"] < LEVEL1_SIZE
            if ln["heading_font"] and ln["size"] >= 18:
                title = (title + " " if title else "") + ln["text"]
            if is_h1 or is_h2:
                if prev and prev["heading_level"] == (1 if is_h1 else 2) and not para:
                    section["heading"] += " " + ln["text"]   # heading wrapped onto a second line
                else:
                    seen_level1 = seen_level1 or is_h1
                    section = {"heading": ln["text"], "level": 1 if is_h1 else 2,
                               "page_start": pno, "page_end": pno, "paragraphs": []}
                    sections.append(section)
                prev = {"heading_level": 1 if is_h1 else 2}
                para = None
                continue
            starts_item = bool(re.match(r"^(•|o\s|[A-Z]\d{4}\b)", ln["text"]))
            new_para = (para is None or starts_item or ln["break_before"] or
                        ln["y"] - para["last_y"] > LINE_GAP or ln["size"] != para["size"])
            if new_para:
                para = {"page": pno, "text": ln["text"], "last_y": ln["y"], "size": ln["size"]}
                section["paragraphs"].append(para)
            else:
                para["text"] += " " + ln["text"]
                para["last_y"] = ln["y"]
            section["page_end"] = pno
            prev = {"heading_level": 0}

    sections.insert(1, sidebar)
    for s in sections:
        s["paragraphs"] = _join_across_pages(
            [{"page": p["page"], "page_end": p["page"], "text": p["text"]} for p in s["paragraphs"]])
        _split_code_table(s)
        if s["heading"].startswith("Policy History"):
            s["excluded_from_diff"] = True   # raw text kept for reference; never diffed
    sections = [s for s in sections if s["paragraphs"] or s["level"] > 0]

    full = "\n".join(p["text"] for s in sections for p in s["paragraphs"])
    # two number styles seen so far: MP.028.14 (Beds and Mattresses) and 2026T0553II (Surgery of the Knee); else Unknown
    num = re.search(r"Policy Number:\s*([A-Z]{2}\.\d{3}\.\d+|\d{4}[A-Z]\d{4}[A-Z]*)", full)
    eff = re.search(r"Effective Date:\s*([A-Za-z]+ \d{1,2}, \d{4})", full)
    return {
        "source_file": None if isinstance(pdf, bytes) else str(pdf),
        "page_count": len(doc),
        "title": title,
        "policy_number": num.group(1) if num else "Unknown",
        "effective_date": (_parse_date(eff.group(1)) if eff else None) or "Unknown",
        "sections": sections,
    }


def render(result):
    lines = [f"Title: {result['title']}", f"Policy number: {result['policy_number']}",
             f"Effective date: {result['effective_date']}", f"Pages: {result['page_count']}", ""]
    for s in result["sections"]:
        pages = (f"p.{s['page_start']}" if s["page_start"] == s["page_end"]
                 else f"pp.{s['page_start']}-{s['page_end']}")
        lines.append(f"{'  ' * max(s['level'] - 1, 0)}[{'H' + str(s['level']) if s['level'] else 'front'}] {s['heading']}  ({pages})")
        pad = "  " * max(s["level"], 1)
        if s.get("excluded_from_diff"):
            lines.append(f"{pad}(raw text; excluded from diffs)")
        for p in s["paragraphs"]:
            pg = f"p.{p['page']}" if p["page"] == p["page_end"] else f"pp.{p['page']}-{p['page_end']}"
            lines.append(f"{pad}({pg}) {p['text']}")
        for r in s.get("code_table", []):
            lines.append(f"{pad}(p.{r['page']}) [{r['code']}] {r['description']}")
    return "\n".join(lines)


if __name__ == "__main__":
    result = extract_policy(sys.argv[1])
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    print(render(result))
