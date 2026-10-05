"""
Raw source document readers. Each returns {"filename", "doc_type", "text", "tables"},
where text is rendered with literal, citable line/row/header markers baked in, so that a
later extraction prompt's provenance.locator is a copy of something it actually saw
(e.g. "L7" or "row 4") rather than something it has to compute or guess.

"tables" holds every table whose row/column structure the file format states
explicitly (a CSV, a markdown pipe table, a Word or PowerPoint table), as
{"locator", "columns", "rows"}. PowerPoint readers also return "charts", the data of
native charts as {"locator", "chart"}. These are captured deterministically, cell for cell,
rather than left for the model to re-transcribe -- a model copying a table can drop or
alter a cell, and nothing downstream would notice. PDF text carries no reliable table
structure, so PDF tables reach extraction only as text lines.
"""
from __future__ import annotations

import csv
import email
import re
from email.policy import default as email_policy
from pathlib import Path


def read_markdown(path) -> dict:
    doc = _read_lined(path, "markdown")
    doc["tables"] = _markdown_tables(Path(path).read_text().splitlines())
    return doc


def read_text(path) -> dict:
    return _read_lined(path, "text")


def _read_lined(path, doc_type) -> dict:
    path = Path(path)
    lines = path.read_text().splitlines()
    text = "\n".join(f"L{i + 1}: {line}" for i, line in enumerate(lines))
    return {"filename": path.name, "doc_type": doc_type, "text": text, "tables": []}


_PIPE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _pipe_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _markdown_tables(lines: list[str]) -> list[dict]:
    """GitHub-style pipe tables: a header row, a --- separator row, then body rows."""
    tables, i = [], 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _PIPE_SEPARATOR.match(lines[i + 1]):
            columns = _pipe_cells(lines[i])
            j = i + 2
            rows = []
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(_pipe_cells(lines[j]))
                j += 1
            if rows:
                tables.append({"locator": f"L{i + 1}-L{j}", "columns": columns, "rows": rows})
            i = j
        else:
            i += 1
    return tables


def read_csv(path) -> dict:
    path = Path(path)
    with path.open(newline="") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return {"filename": path.name, "doc_type": "csv", "text": "", "tables": []}
    header, data_rows = rows[0], rows[1:]
    lines = [f"header: {','.join(header)}"]
    for i, row in enumerate(data_rows):
        pairs = ", ".join(f"{col}={val}" for col, val in zip(header, row))
        lines.append(f"row {i + 1}: {pairs}")
    tables = [{"locator": f"rows 1-{len(data_rows)}", "columns": header, "rows": data_rows}] if data_rows else []
    return {"filename": path.name, "doc_type": "csv", "text": "\n".join(lines), "tables": tables}


def read_pdf(path) -> dict:
    """One marker per non-empty extracted line, prefixed with its page: "p2 L14"."""
    from pypdf import PdfReader

    path = Path(path)
    lines = []
    for page_num, page in enumerate(PdfReader(str(path)).pages, start=1):
        page_lines = [ln for ln in (page.extract_text() or "").splitlines() if ln.strip()]
        lines += [f"p{page_num} L{i + 1}: {ln}" for i, ln in enumerate(page_lines)]
    return {"filename": path.name, "doc_type": "pdf", "text": "\n".join(lines), "tables": []}


_DIVIDER_LAYOUT = re.compile(r"section|divider", re.I)
_TITLE_LAYOUT = re.compile(r"^title slide$|^title\.|^cover|^title$", re.I)
_CLOSING_LAYOUT = re.compile(r"closing|thank|contact|^end\b", re.I)


def _slide_role(slide, index: int, count: int) -> str | None:
    """Framing role from the slide's layout name or placeholder types, so extraction can
    tell a title, divider, or closing slide from one that carries evidence."""
    from pptx.enum.shapes import PP_PLACEHOLDER

    name = slide.slide_layout.name or ""
    if _DIVIDER_LAYOUT.search(name):
        return "section divider"
    if _CLOSING_LAYOUT.search(name):
        return "closing"
    center_title = any(sh.is_placeholder and sh.placeholder_format.type == PP_PLACEHOLDER.CENTER_TITLE
                       for sh in slide.shapes)
    if _TITLE_LAYOUT.search(name) or center_title:
        return "closing" if index == count and index > 1 else "title"
    return None


def _walk_shapes(shapes):
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    for sh in shapes:
        if sh.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk_shapes(sh.shapes)
        else:
            yield sh


def _chart_payload(chart) -> dict | None:
    """The chart's own data, copied exactly. None for chart types without shared
    categories (scatter, bubble) or with missing values."""
    kind = chart.chart_type.name if chart.chart_type is not None else ""
    if kind.startswith(("XY_", "BUBBLE")):
        return None
    plot = chart.plots[0]
    categories = [str(c) for c in plot.categories]
    series = []
    for pl in chart.plots:
        for se in pl.series:
            vals = list(se.values)
            if len(vals) != len(categories) or any(v is None for v in vals):
                return None
            series.append({"name": se.name or "Series", "values": [int(v) if float(v).is_integer() else v for v in vals]})
    if len(categories) < 2 or not series:
        return None
    payload = {"type": "line" if kind.startswith("LINE") else "bar",
               "title": chart.chart_title.text_frame.text if chart.has_title else "",
               "categories": categories, "series": series}
    if kind.startswith("BAR"):
        payload["orientation"] = "horizontal"
    return payload


def read_pptx(path) -> dict:
    """Per slide: "S3 role", "S3 title", "S3 B<n>" body paragraphs, "S3 T<n>" tables,
    "S3 C<n>" charts, "S3 G<n>" pictures, "S3 notes". Tables and charts are also
    returned structurally."""
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    path = Path(path)
    prs = Presentation(str(path))
    lines, tables, charts = [], [], []
    count = len(prs.slides)
    for s, slide in enumerate(prs.slides, start=1):
        role = _slide_role(slide, s, count)
        if role:
            lines.append(f"S{s} role: {role} (layout \"{slide.slide_layout.name}\")")
        title_shape = slide.shapes.title
        if title_shape is not None and title_shape.text_frame.text.strip():
            lines.append(f"S{s} title: {title_shape.text_frame.text.strip()}")
        b = t = c = g = 0
        for sh in _walk_shapes(slide.shapes):
            if sh.shape_type == MSO_SHAPE_TYPE.PICTURE:
                # A picture may be a chart flattened to an image (Google Slides does this
                # to native charts on conversion). Its data cannot be read, so it is
                # marked rather than silently skipped.
                g += 1
                lines.append(f"S{s} G{g}: picture, no extractable data")
                continue
            if title_shape is not None and sh.shape_id == title_shape.shape_id:
                continue
            if sh.has_text_frame:
                for para in sh.text_frame.paragraphs:
                    text = "".join(r.text for r in para.runs).strip()
                    if text:
                        b += 1
                        lines.append(f"S{s} B{b}: {text}")
            if getattr(sh, "has_table", False) and sh.has_table:
                t += 1
                grid = [[cell.text.strip() for cell in row.cells] for row in sh.table.rows]
                header, body = grid[0], grid[1:]
                lines.append(f"S{s} T{t} header: {','.join(header)}")
                for r, row in enumerate(body, start=1):
                    lines.append(f"S{s} T{t} row {r}: " + ", ".join(f"{h}={v}" for h, v in zip(header, row)))
                if body and len(header) >= 2:
                    tables.append({"locator": f"S{s} T{t}", "columns": header, "rows": body})
            if getattr(sh, "has_chart", False) and sh.has_chart:
                c += 1
                payload = _chart_payload(sh.chart)
                plot = sh.chart.plots[0]
                cats = [str(x) for x in plot.categories]
                for se in plot.series:
                    pairs = ", ".join(f"{k}={v}" for k, v in zip(cats, se.values))
                    lines.append(f"S{s} C{c}: {se.name}: {pairs}")
                if payload:
                    charts.append({"locator": f"S{s} C{c}", "chart": payload})
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                lines.append(f"S{s} notes: {' '.join(notes.split())}")
    return {"filename": path.name, "doc_type": "pptx", "text": "\n".join(lines),
            "tables": tables, "charts": charts}


def read_docx(path) -> dict:
    """Paragraphs as "P<n>", tables as "T<n> header" / "T<n> row <r>", in document order."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    path = Path(path)
    document = docx.Document(str(path))
    lines, tables = [], []
    p_num = t_num = 0
    for block in document.element.body.iterchildren():
        tag = block.tag.rsplit("}", 1)[-1]
        if tag == "p":
            text = Paragraph(block, document).text
            if text.strip():
                p_num += 1
                lines.append(f"P{p_num}: {text}")
        elif tag == "tbl":
            t_num += 1
            grid = [[cell.text.strip() for cell in row.cells] for row in Table(block, document).rows]
            if not grid:
                continue
            header, body = grid[0], grid[1:]
            lines.append(f"T{t_num} header: {','.join(header)}")
            for r, row in enumerate(body, start=1):
                lines.append(f"T{t_num} row {r}: " + ", ".join(f"{c}={v}" for c, v in zip(header, row)))
            if body:
                tables.append({"locator": f"T{t_num}", "columns": header, "rows": body})
    return {"filename": path.name, "doc_type": "docx", "text": "\n".join(lines), "tables": tables}


def read_email(path) -> dict:
    path = Path(path)
    msg = email.message_from_bytes(path.read_bytes(), policy=email_policy)
    lines = []
    for header in ("From", "To", "Subject"):
        if msg.get(header):
            lines.append(f"header: {header}: {msg.get(header)}")
    body = msg.get_body(preferencelist=("plain",))
    body_text = body.get_content() if body else msg.get_payload()
    for i, line in enumerate(body_text.splitlines()):
        lines.append(f"body L{i + 1}: {line}")
    return {"filename": path.name, "doc_type": "email", "text": "\n".join(lines), "tables": []}


def read_html(path) -> dict:
    """Visible text of a saved web page or HTML email, one block per line ("L<n>").
    Scripts and styles are skipped; tables come through as one line per row."""
    from html.parser import HTMLParser

    blocks = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
              "blockquote", "pre", "table", "ul", "ol", "header", "footer", "title"}

    class _Text(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.lines, self.cur, self.skip = [], [], 0

        def _flush(self):
            line = " ".join(" ".join(self.cur).split())
            if line:
                self.lines.append(line)
            self.cur = []

        def handle_starttag(self, tag, attrs):
            if tag in ("script", "style", "head"):
                self.skip += 1 if tag != "head" else 0
            if tag in blocks:
                self._flush()
            if tag in ("td", "th"):
                self.cur.append("|")

        def handle_endtag(self, tag):
            if tag in ("script", "style") and self.skip:
                self.skip -= 1
            if tag in blocks:
                self._flush()

        def handle_data(self, data):
            if not self.skip and data.strip():
                self.cur.append(data.strip())

    path = Path(path)
    parser = _Text()
    parser.feed(path.read_text(errors="replace"))
    parser._flush()
    lines = [x for x in (ln.strip("| ").replace(" |", ",") for ln in parser.lines) if x]
    text = "\n".join(f"L{i + 1}: {line}" for i, line in enumerate(lines))
    return {"filename": path.name, "doc_type": "html", "text": text, "tables": []}


def read_json(path) -> dict:
    """A JSON export (for example a chat or form export), pretty-printed so each value
    sits on its own citable line."""
    import json as _json
    path = Path(path)
    try:
        pretty = _json.dumps(_json.loads(path.read_text()), indent=1, ensure_ascii=False)
    except ValueError:
        pretty = path.read_text(errors="replace")
    text = "\n".join(f"L{i + 1}: {line}" for i, line in enumerate(pretty.splitlines()))
    return {"filename": path.name, "doc_type": "json", "text": text, "tables": []}


def read_rtf(path) -> dict:
    """Crude RTF to text: control words and groups stripped. Good enough for notes and
    memos; a document with complex formatting is better converted to .docx first."""
    path = Path(path)
    raw = path.read_text(errors="replace")
    raw = re.sub(r"\\par[d]?\b", "\n", raw)
    raw = re.sub(r"\{\\\*[^{}]*\}", "", raw)
    raw = re.sub(r"\\'[0-9a-f]{2}", "", raw)
    raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", raw)
    raw = raw.replace("{", "").replace("}", "")
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    text = "\n".join(f"L{i + 1}: {line}" for i, line in enumerate(lines))
    return {"filename": path.name, "doc_type": "rtf", "text": text, "tables": []}


def read_xlsx(path) -> dict:
    """Each sheet as "X<n> header" and "X<n> row <r>" lines, values as displayed
    (cached formula results, not formulas). Every sheet with a header and at least one
    row is also returned as a structured table."""
    import openpyxl

    path = Path(path)
    wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    lines, tables = [], []
    for n, ws in enumerate(wb.worksheets, start=1):
        rows = [["" if v is None else str(v).strip() for v in row] for row in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(r)]
        if not rows:
            continue
        width = max(i + 1 for r in rows for i, v in enumerate(r) if v)
        rows = [r[:width] + [""] * (width - len(r[:width])) for r in rows]
        header, body = rows[0], rows[1:]
        lines.append(f"X{n} sheet: {ws.title}")
        lines.append(f"X{n} header: {','.join(header)}")
        for r, row in enumerate(body, start=1):
            lines.append(f"X{n} row {r}: " + ", ".join(f"{h}={v}" for h, v in zip(header, row) if v))
        if body and len(header) >= 2:
            tables.append({"locator": f"X{n}", "columns": header, "rows": body})
    return {"filename": path.name, "doc_type": "xlsx", "text": "\n".join(lines), "tables": tables}


_DISPATCH = {".md": read_markdown, ".csv": read_csv, ".eml": read_email, ".txt": read_text,
             ".pdf": read_pdf, ".docx": read_docx, ".pptx": read_pptx,
             ".html": read_html, ".htm": read_html, ".json": read_json, ".rtf": read_rtf,
             ".xlsx": read_xlsx, ".xlsm": read_xlsx}
KNOWN_EXTENSIONS = frozenset(_DISPATCH)

# Every line-marker shape the readers above emit, for anything that needs to strip or
# parse them (stub extraction, payload verification).
MARKER_RE = re.compile(r"^(L\d+|row \d+|body L\d+|header|p\d+ L\d+|P\d+|T\d+ header|T\d+ row \d+|"
                       r"X\d+ (?:sheet|header|row \d+)|"
                       r"S\d+ (?:role|title|notes|B\d+|C\d+|G\d+|T\d+ header|T\d+ row \d+)): ?(.*)$")


def read_source(path) -> dict:
    path = Path(path)
    reader = _DISPATCH.get(path.suffix.lower())
    if reader is None:
        raise ValueError(f"no reader for file extension {path.suffix!r} ({path.name})")
    return reader(path)
