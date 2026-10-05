"""
Intake for the no-model draft mode (the plugin). Turns whatever the user hands over --
files, folders, pasted text Claude saved, image transcriptions, Google files -- into a
numbered source registry with citable, marked text, and says plainly what it could not
read and what to do about it.

Nothing here calls a model. Reading is deterministic (deck_tool.ingest.readers). The
judgment work -- what matters, what to cut, what conflicts -- happens in the host
Claude conversation and comes back as draft.json (see build.py).

Project folder layout:
  inbox/          pasted text, email bodies, notes Claude saved for the user
  transcripts/    Claude's transcription of an image or a scanned PDF, named
                  <original filename>.md, read in place of the original
  converted/      legacy Office files converted by LibreOffice
  sources_text/   SRCnn.txt: the marked text every citation points into
  intake.json     the registry; intake_report.md, the readable version

Source ids are stable across re-runs (keyed by path), so a transcription added later
does not renumber everything Claude already cited.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..common import load_json, save_json
from ..ingest.readers import KNOWN_EXTENSIONS, read_source, read_text

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp"})
# Legacy and open formats LibreOffice can convert into one the readers handle.
CONVERTIBLE = {".doc": "docx", ".odt": "docx", ".wpd": "docx", ".ppt": "pptx", ".odp": "pptx",
               ".pps": "pptx", ".ppsx": "pptx", ".xls": "xlsx", ".ods": "xlsx"}
GOOGLE_SHORTCUTS = frozenset({".gdoc", ".gslides", ".gsheet"})
NEEDS_EXPORT = {".key": "Keynote: export to .pptx or PDF from Keynote", ".pages": "Pages: export to .docx or PDF",
                ".numbers": "Numbers: export to .xlsx or CSV", ".msg": "Outlook message: save as .eml or paste the text"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
PROJECT_DIRS = {"sources_text", "converted", "out", "transcripts"}
MIN_PDF_CHARS = 80  # below this a PDF is treated as scanned: its text needs transcribing from the page images


def soffice_path() -> str | None:
    for cand in ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        found = shutil.which(cand) or (cand if Path(cand).exists() else None)
        if found:
            return found
    return None


def _convert(path: Path, target: str, dest: Path) -> Path | None:
    office = soffice_path()
    if not office:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([office, "--headless", "--convert-to", target, "--outdir", str(dest), str(path)],
                       capture_output=True, timeout=180, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = dest / f"{path.stem}.{target}"
    return out if out.exists() else None


def _gather(inputs: list[Path], project: Path) -> list[Path]:
    """Every file named, and every file under every folder named, in a stable order.
    The project's own working folders are skipped; inbox/ is read like any input."""
    files = []
    for p in inputs:
        p = p.expanduser().resolve()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                rel = f.relative_to(p).parts
                if (f.is_file() and f.name not in SKIP_NAMES and not any(x.startswith(".") for x in rel)
                        and not (p == project.resolve() and rel[0] in PROJECT_DIRS | {"intake.json", "draft.json"})
                        and not (f.suffix.lower() == ".md" and f.parent.name == "transcripts")):
                    files.append(f)
        elif p.is_file():
            files.append(p)
    inbox = (project / "inbox").resolve()
    if inbox.is_dir():
        files += [f for f in sorted(inbox.rglob("*")) if f.is_file() and f.name not in SKIP_NAMES]
    seen, out = set(), []
    for f in files:
        if f not in seen and f.name not in ("intake_report.md", "REVIEW.md"):
            seen.add(f)
            out.append(f)
    return out


def _transcript_for(path: Path, project: Path) -> Path | None:
    for cand in (project / "transcripts" / f"{path.name}.md", project / "transcripts" / f"{path.name}.txt"):
        if cand.exists() and cand.read_text().strip():
            return cand
    return None


def _read_one(path: Path, project: Path, google_drive=None) -> dict:
    """{status, doc_type, note, doc (reader output or None)} for one file.
    status: read | needs_vision | needs_connector | unsupported | error."""
    ext = path.suffix.lower()
    transcript = _transcript_for(path, project)
    if ext in IMAGE_EXTENSIONS:
        if transcript:
            doc = read_text(transcript)
            doc.update(filename=path.name, doc_type="image")
            return {"status": "read", "doc_type": "image", "doc": doc,
                    "note": "Text and figures transcribed by Claude from the image. Check figures against the image."}
        return {"status": "needs_vision", "doc_type": "image", "doc": None,
                "note": f"Image. View it and write what it shows to transcripts/{path.name}.md, then re-run intake."}
    if ext in GOOGLE_SHORTCUTS:
        return _google(str(path), project, google_drive)
    if ext in NEEDS_EXPORT:
        return {"status": "unsupported", "doc_type": ext.lstrip("."), "doc": None, "note": NEEDS_EXPORT[ext]}
    if ext in CONVERTIBLE:
        conv = _convert(path, CONVERTIBLE[ext], project / "converted")
        if conv is None:
            return {"status": "unsupported", "doc_type": ext.lstrip("."), "doc": None,
                    "note": f"Legacy {ext} file and LibreOffice is not available to convert it. "
                            f"Save it as .{CONVERTIBLE[ext]} or PDF."}
        doc = read_source(conv)
        doc["filename"] = path.name
        return {"status": "read", "doc_type": doc["doc_type"], "doc": doc,
                "note": f"Converted from {ext} to .{CONVERTIBLE[ext]} by LibreOffice."}
    if ext not in KNOWN_EXTENSIONS:
        try:
            raw = path.read_text()
        except (UnicodeDecodeError, OSError):
            return {"status": "unsupported", "doc_type": ext.lstrip(".") or "unknown", "doc": None,
                    "note": "No reader for this file type and it is not plain text."}
        doc = read_text(path)
        return {"status": "read" if raw.strip() else "error", "doc_type": "text", "doc": doc,
                "note": f"Read as plain text (no dedicated reader for {ext or 'files without an extension'})."}
    try:
        doc = read_source(path)
    except Exception as e:  # a corrupt or password-protected file must not stop the rest
        return {"status": "error", "doc_type": ext.lstrip("."), "doc": None, "note": f"Could not read: {e}"[:300]}
    if ext == ".pdf" and len(doc["text"]) < MIN_PDF_CHARS:
        if transcript:
            doc = read_text(transcript)
            doc.update(filename=path.name, doc_type="pdf_scan")
            return {"status": "read", "doc_type": "pdf_scan", "doc": doc,
                    "note": "Scanned PDF transcribed by Claude from the page images. Check figures against the PDF."}
        return {"status": "needs_vision", "doc_type": "pdf", "doc": None,
                "note": f"PDF has almost no extractable text (likely scanned). View its pages and write the "
                        f"content to transcripts/{path.name}.md, then re-run intake."}
    if not doc["text"].strip():
        return {"status": "error", "doc_type": doc["doc_type"], "doc": doc, "note": "File read but contains no text."}
    pictures = doc["text"].count(": picture, no extractable data")
    note = (f"{pictures} picture(s) in the deck carry no readable data. If one is a chart or table that matters, "
            f"view it and add a transcript." if pictures else "")
    return {"status": "read", "doc_type": doc["doc_type"], "doc": doc, "note": note}


def _google(ref: str, project: Path, drive=None) -> dict:
    """A Google Doc, Slides deck, or Sheet, through the engine's own Drive access when
    this machine has it (OAuth files in ~/.config/deck-tool). Otherwise the host Claude
    should fetch it through a Google Drive connector and save the text into inbox/."""
    from ..ingest.google import fetch
    try:
        if drive is None:
            from ..gslides.auth import drive_service
            drive = drive_service()
        doc = fetch(drive, ref, project / "converted")
    except Exception as e:
        return {"status": "needs_connector", "doc_type": "google", "doc": None,
                "note": f"Could not read through the engine's Google access ({str(e)[:120]}). Fetch it with a "
                        f"Google Drive connector and save the text to inbox/<title>.md, then re-run intake."}
    return {"status": "read", "doc_type": doc["doc_type"], "doc": doc,
            "note": f"Google file exported through Drive ({doc['google']['title']})."}


def intake(project, inputs=(), google_refs=(), drive=None) -> dict:
    project = Path(project)
    project.mkdir(parents=True, exist_ok=True)
    (project / "inbox").mkdir(exist_ok=True)
    (project / "transcripts").mkdir(exist_ok=True)
    text_dir = project / "sources_text"
    text_dir.mkdir(exist_ok=True)

    prior = load_json(project / "intake.json", {}) or {}
    ids_by_key = {s["key"]: s["id"] for s in prior.get("sources", [])}
    next_n = max([int(s["id"][3:]) for s in prior.get("sources", [])] + [0]) + 1

    items = [(str(f), f, None) for f in _gather([Path(x) for x in inputs], project)]
    items += [(f"google:{r}", None, r) for r in google_refs]
    # Previously registered Google sources are re-read on every intake run.
    for s in prior.get("sources", []):
        if s["key"].startswith("google:") and s["key"] not in {k for k, _, _ in items}:
            items.append((s["key"], None, s["key"][len("google:"):]))
    # Previously registered files stay registered even when this run names only new inputs.
    for s in prior.get("sources", []):
        if not s["key"].startswith("google:") and s["key"] not in {k for k, _, _ in items} and Path(s["key"]).exists():
            items.append((s["key"], Path(s["key"]), None))

    sources, captured = [], {}
    for key, path, gref in items:
        sid = ids_by_key.get(key)
        if sid is None:
            sid = f"SRC{next_n:02d}"
            next_n += 1
        r = _google(gref, project, drive) if gref else _read_one(path, project, drive)
        doc = r["doc"]
        entry = {"id": sid, "key": key, "filename": doc["filename"] if doc else (path.name if path else gref),
                 "path": str(path) if path else None, "doc_type": r["doc_type"], "status": r["status"],
                 "note": r["note"]}
        if doc and r["status"] == "read":
            (text_dir / f"{sid}.txt").write_text(doc["text"])
            entry.update(text_file=f"sources_text/{sid}.txt", lines=doc["text"].count("\n") + 1,
                         chars=len(doc["text"]), tables=len(doc.get("tables", [])), charts=len(doc.get("charts", [])))
            if doc.get("google"):
                entry["google"] = doc["google"]
            captured[sid] = {"tables": doc.get("tables", []), "charts": doc.get("charts", [])}
        sources.append(entry)

    sources.sort(key=lambda s: int(s["id"][3:]))
    missing = [str(x) for x in inputs if not Path(x).expanduser().exists()]
    out = {"project": str(project.resolve()), "sources": sources, "captured": captured, "not_found": missing}
    save_json(project / "intake.json", out)
    (project / "intake_report.md").write_text(report(out))
    return out


def report(out: dict) -> str:
    srcs = out["sources"]
    read = [s for s in srcs if s["status"] == "read"]
    lines = ["# Intake", "", f"{len(read)} of {len(srcs)} source(s) readable.", "",
             "| Id | File | Type | Status | Size | Note |", "|---|---|---|---|---|---|"]
    for s in srcs:
        size = f"{s.get('lines', 0)} lines" if s["status"] == "read" else ""
        extra = []
        if s.get("tables"):
            extra.append(f"{s['tables']} table(s) captured")
        if s.get("charts"):
            extra.append(f"{s['charts']} chart(s) captured")
        note = "; ".join(x for x in [s["note"], ", ".join(extra)] if x)
        lines.append(f"| {s['id']} | {s['filename']} | {s['doc_type']} | {s['status']} | {size} | {note} |")
    if out.get("not_found"):
        lines += ["", "## Inputs not found", ""] + [f"- {x}" for x in out["not_found"]]
    todo = [s for s in srcs if s["status"] != "read"]
    if todo:
        lines += ["", "## Needs attention before drafting", ""]
        lines += [f"- **{s['id']} {s['filename']}** ({s['status']}): {s['note']}" for s in todo]
    caps = [(sid, t["locator"], ", ".join(t["columns"])) for sid, c in out["captured"].items() for t in c["tables"]]
    caps += [(sid, c_["locator"], c_["chart"].get("title") or "chart") for sid, c in out["captured"].items()
             for c_ in c["charts"]]
    if caps:
        lines += ["", "## Tables and charts copied cell for cell", "",
                  "Cite these by source and locator with kind table or chart; the build copies the data from the "
                  "file rather than from the draft.", ""]
        lines += [f"- {sid} {loc}: {what}" for sid, loc, what in caps]
    return "\n".join(lines) + "\n"
