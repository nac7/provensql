"""Render docs/paper1_draft.md to a preprint PDF (+ a print-quality HTML).

Pure-Python (markdown + xhtml2pdf) so it runs on Windows with no LaTeX/GTK.
xhtml2pdf's built-in Helvetica can't encode symbols like the section sign,
arrows, or <=, so we embed the DejaVu family (full Unicode coverage, bundled
with matplotlib) via @font-face. That lets the PDF render the same glyphs as
the HTML -- no ASCII substitution needed.

Requires: pip install markdown xhtml2pdf matplotlib
"""
import glob
import os
import sys
from pathlib import Path

import markdown

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/paper1_draft.md")
OUT_PDF = SRC.with_suffix(".pdf")
OUT_HTML = SRC.with_suffix(".html")


def dejavu_dir():
    import matplotlib
    d = os.path.join(os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf")
    if not glob.glob(os.path.join(d, "DejaVuSerif.ttf")):
        raise SystemExit("DejaVu fonts not found under matplotlib; pip install matplotlib")
    return d.replace("\\", "/")


FONTS = dejavu_dir()


def register_fonts():
    """Register DejaVu with reportlab and expose the family names to xhtml2pdf's
    font table directly. xhtml2pdf's own @font-face handling is unreliable for
    local files (it writes an empty temp file), so we bypass it entirely."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from xhtml2pdf.default import DEFAULT_FONT

    families = {
        "DJSerif": ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf",
                    "DejaVuSerif-Italic.ttf", "DejaVuSerif-BoldItalic.ttf"),
        "DJSans": ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", None, None),
        "DJMono": ("DejaVuSansMono.ttf", "DejaVuSansMono-Bold.ttf", None, None),
    }
    for fam, (reg, bold, ital, boldital) in families.items():
        pdfmetrics.registerFont(TTFont(fam, f"{FONTS}/{reg}"))
        names = {"normal": fam}
        if bold:
            pdfmetrics.registerFont(TTFont(f"{fam}-Bold", f"{FONTS}/{bold}")); names["bold"] = f"{fam}-Bold"
        if ital:
            pdfmetrics.registerFont(TTFont(f"{fam}-Italic", f"{FONTS}/{ital}")); names["italic"] = f"{fam}-Italic"
        if boldital:
            pdfmetrics.registerFont(TTFont(f"{fam}-BoldItalic", f"{FONTS}/{boldital}")); names["boldItalic"] = f"{fam}-BoldItalic"
        pdfmetrics.registerFontFamily(fam, **names)
        DEFAULT_FONT[fam.lower()] = fam


register_fonts()

CSS = """
@page { size: letter; margin: 2.0cm 2.2cm; }
body { font-family: "DJSerif", serif; font-size: 9.8pt; line-height: 1.4; color: #111; }
h1 { font-size: 17pt; line-height: 1.2; margin: 0 0 4pt 0; }
h2 { font-size: 12.5pt; margin: 15pt 0 4pt 0; border-bottom: 1px solid #bbb; padding-bottom: 2pt; }
h3 { font-size: 10.8pt; margin: 11pt 0 3pt 0; }
h4 { font-size: 9.8pt; margin: 9pt 0 2pt 0; font-style: italic; }
p, li { margin: 0 0 5pt 0; }
code { font-family: "DJMono", monospace; font-size: 8.4pt; background: #f2f2f2; }
pre { font-family: "DJMono", monospace; font-size: 8pt; background: #f5f5f5;
      border: 1px solid #ddd; padding: 6pt; line-height: 1.25; }
table { border-collapse: collapse; width: 100%; font-size: 8.2pt; margin: 6pt 0; }
th, td { border: 1px solid #999; padding: 3pt 5pt; text-align: left; vertical-align: top; }
th { background: #ececec; font-family: "DJSans", sans-serif; }
a { color: #1a4f8b; text-decoration: none; }
hr { border: 0; border-top: 1px solid #ccc; }
"""


def to_html(text):
    return markdown.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"])


def page(body, css):
    return (f"<!DOCTYPE html><html><head><meta charset='utf-8'><style>{css}</style>"
            f"</head><body>{body}</body></html>")


md_text = SRC.read_text(encoding="utf-8")
html = page(to_html(md_text), CSS)

OUT_HTML.write_text(html, encoding="utf-8")
print(f"wrote {OUT_HTML}")

from xhtml2pdf import pisa  # noqa: E402


def link_callback(uri, rel):
    """Resolve @font-face src URLs to real local paths. Without this xhtml2pdf
    tries to 'download' the local path and writes an empty temp file, which
    reportlab then can't open."""
    p = uri
    if p.startswith("file:///"):
        p = p[8:]
    return p if os.path.isfile(p) else uri


with open(OUT_PDF, "wb") as f:
    result = pisa.CreatePDF(html, dest=f, encoding="utf-8", link_callback=link_callback)
if result.err:
    print(f"PDF generation had {result.err} error(s)")
    sys.exit(1)
print(f"wrote {OUT_PDF} ({OUT_PDF.stat().st_size} bytes)")
