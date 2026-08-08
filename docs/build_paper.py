"""Render docs/paper1_draft.md to a preprint PDF (+ a print-quality HTML).

Pure-Python (markdown + xhtml2pdf) so it runs on Windows with no LaTeX/GTK.
xhtml2pdf's built-in fonts don't carry many math/arrow glyphs, so we map the
few used in the draft to ASCII for the PDF path; the HTML fallback keeps them.
"""
import sys
from pathlib import Path

import markdown

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/paper1_draft.md")
OUT_PDF = SRC.with_suffix(".pdf")
OUT_HTML = SRC.with_suffix(".html")

md_text = SRC.read_text(encoding="utf-8")

CSS = """
@page { size: letter; margin: 2.0cm 2.2cm; }
body { font-family: "Georgia","Times New Roman",serif; font-size: 10.5pt;
       line-height: 1.42; color: #111; }
h1 { font-size: 19pt; line-height: 1.2; margin: 0 0 4pt 0; }
h2 { font-size: 13.5pt; margin: 16pt 0 4pt 0; border-bottom: 1px solid #bbb;
     padding-bottom: 2pt; }
h3 { font-size: 11.5pt; margin: 12pt 0 3pt 0; }
h4 { font-size: 10.5pt; margin: 10pt 0 2pt 0; font-style: italic; }
p, li { margin: 0 0 6pt 0; }
code { font-family: "Consolas","Courier New",monospace; font-size: 9pt;
       background: #f2f2f2; }
pre { font-family: "Consolas","Courier New",monospace; font-size: 8.5pt;
      background: #f5f5f5; border: 1px solid #ddd; padding: 6pt;
      line-height: 1.25; }
table { border-collapse: collapse; width: 100%; font-size: 8.8pt;
        margin: 6pt 0; }
th, td { border: 1px solid #999; padding: 3pt 5pt; text-align: left;
         vertical-align: top; }
th { background: #ececec; font-family: "Helvetica","Arial",sans-serif; }
a { color: #1a4f8b; text-decoration: none; }
hr { border: 0; border-top: 1px solid #ccc; }
"""

# xhtml2pdf glyph-safe substitutions (PDF only)
ASCII = {
    "→": " -> ", "↔": " <-> ", "≤": " <= ", "≥": " >= ",
    "×": "x", "≈": "~=", "–": "-", "—": " -- ",
    "§": "Sec. ", "•": "-", "‑": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...",
}

def to_html(text):
    return markdown.markdown(
        text, extensions=["tables", "fenced_code", "toc", "sane_lists"]
    )

def page(body, css):
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{css}</style></head><body>{body}</body></html>"

# 1) print-quality HTML (keeps unicode)
OUT_HTML.write_text(page(to_html(md_text), CSS), encoding="utf-8")
print(f"wrote {OUT_HTML}")

# 2) PDF via xhtml2pdf (ASCII-normalized)
pdf_text = md_text
for u, a in ASCII.items():
    pdf_text = pdf_text.replace(u, a)
html_for_pdf = page(to_html(pdf_text), CSS)

from xhtml2pdf import pisa  # noqa: E402

with open(OUT_PDF, "wb") as f:
    result = pisa.CreatePDF(html_for_pdf, dest=f, encoding="utf-8")
if result.err:
    print(f"PDF generation had {result.err} error(s)")
    sys.exit(1)
print(f"wrote {OUT_PDF} ({OUT_PDF.stat().st_size} bytes)")
