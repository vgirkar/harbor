"""Build Harbor_Technical_Deep_Dive.docx from the chapter markdown files.

pandoc gives the structure, python-docx the layout (A4, Calibri, navy headings, shaded table headers,
page break before every chapter). Usage: /usr/bin/python3 build.py [out.docx]
"""

import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

here = Path(__file__).parent
parts = sorted(here.glob("[0-9][0-9]_*.md"))
combined = here / "_combined.md"
combined.write_text("\n\n".join(p.read_text(encoding="utf-8") for p in parts), encoding="utf-8")
raw = here / "_raw.docx"
out = Path(sys.argv[1]) if len(sys.argv) > 1 else here.parent / "Harbor_Technical_Deep_Dive.docx"

subprocess.run(["pandoc", str(combined), "-o", str(raw), "--from", "markdown", "--to", "docx"], check=True)
d = Document(str(raw))

for s in d.sections:
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)
    s.top_margin = s.bottom_margin = Cm(1.8)
    s.left_margin = s.right_margin = Cm(2.0)
    s.header_distance = s.footer_distance = Cm(1.0)
    s.gutter = Cm(0)


def insert_ordered(parent, child, successors):
    """Insert `child` before the first existing element whose tag is in `successors` (schema order)."""
    for i, existing in enumerate(list(parent)):
        if existing.tag in {qn(t) for t in successors}:
            parent.insert(i, child)
            return
    parent.append(child)


NAVY = "1F2A44"
GREY = "6B7280"


def set_font(style, name="Calibri", size=10.5, bold=None, color=None, before=None, after=None, line=None):
    style.font.name = name
    style.font.size = Pt(size)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(a), name)
    if bold is not None:
        style.font.bold = bold
    if color is not None:
        style.font.color.rgb = RGBColor.from_string(color)
    pf = style.paragraph_format
    if before is not None:
        pf.space_before = Pt(before)
    if after is not None:
        pf.space_after = Pt(after)
    if line is not None:
        pf.line_spacing = line


st = d.styles
names = {x.name for x in st}
set_font(st["Normal"], size=10.5, after=5, line=1.08)
for name in ("Body Text", "First Paragraph", "Compact"):
    if name in names:
        set_font(st[name], size=10.5, after=5, line=1.08)
set_font(st["Title"], size=24, bold=True, color=NAVY, before=0, after=2)
if "Subtitle" in names:
    set_font(st["Subtitle"], size=13, bold=False, color=GREY, before=0, after=10)
set_font(st["Heading 1"], size=17, bold=True, color=NAVY, before=0, after=8)
set_font(st["Heading 2"], size=12.5, bold=True, color=NAVY, before=12, after=4)
if "Heading 3" in names:
    set_font(st["Heading 3"], size=11, bold=True, color="0F6E8C", before=10, after=3)
for code_style in ("Verbatim Char", "Source Code"):
    if code_style in names:
        cs = st[code_style]
        cs.font.name = "Consolas"
        cs.font.size = Pt(9)
        rpr = cs.element.get_or_add_rPr()
        rf = rpr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rpr.append(rf)
        for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rf.set(qn(a), "Consolas")
if "Source Code" in names:
    sc = st["Source Code"].paragraph_format
    sc.space_after = Pt(4)
    sc.left_indent = Cm(0.4)

for p in d.paragraphs:
    sn = p.style.name
    if sn.startswith("Heading") or sn in ("Title", "Subtitle"):
        p.paragraph_format.keep_with_next = True
    if sn == "Heading 1":
        p.paragraph_format.page_break_before = True
    if p.text.startswith("25 Nov 2026"):
        for r in p.runs:
            r.font.size = Pt(9.5)
            r.font.color.rgb = RGBColor.from_string(GREY)
        p.paragraph_format.space_after = Pt(8)

for t in d.tables:
    tblPr = t._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "bottom", "insideH"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "C7CDD8")
        borders.append(el)
    insert_ordered(tblPr, borders, ("w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription"))
    header_is_blank = all(c.text.strip() == "" for c in t.rows[0].cells)
    for i, row in enumerate(t.rows):
        trPr = row._tr.get_or_add_trPr()
        insert_ordered(trPr, OxmlElement("w:cantSplit"), ("w:trHeight", "w:tblHeader", "w:tblCellSpacing", "w:jc", "w:hidden"))
        for cell in row.cells:
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(1)
                p.paragraph_format.space_before = Pt(1)
                p.paragraph_format.line_spacing = 1.0
                if i == 0:
                    p.paragraph_format.keep_with_next = True
                for r in p.runs:
                    r.font.size = Pt(9)
                    if i == 0 and not header_is_blank:
                        r.font.bold = True
            if i == 0 and not header_is_blank:
                tcPr = cell._tc.get_or_add_tcPr()
                shd = OxmlElement("w:shd")
                shd.set(qn("w:val"), "clear")
                shd.set(qn("w:color"), "auto")
                shd.set(qn("w:fill"), "E8ECF3")
                insert_ordered(tcPr, shd, ("w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark"))
    if header_is_blank and len(t.columns) == 2:
        for row in t.rows:
            row.cells[0].width = Cm(3.4)
            row.cells[1].width = Cm(13.6)
        t.autofit = False
        t.columns[0].width = Cm(3.4)
        t.columns[1].width = Cm(13.6)

d.core_properties.title = "Harbor: Technical Deep Dive for Interviews"
d.core_properties.author = "Vinit Girkar"
d.save(str(out))
combined.unlink(missing_ok=True)
raw.unlink(missing_ok=True)
print("saved", out)
