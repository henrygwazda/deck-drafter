"""
Google Docs, Slides, and Sheets as ingestion sources. Each file is exported through the
Drive API to the matching Office or CSV format and read by the existing reader for that
format, so a Google Doc and the same document saved as .docx produce the same evidence.
Uses the read-only Drive scope: the tool never modifies a source file.

A source can be named by URL, by bare file id, or by a Drive-for-desktop shortcut file
(.gdoc, .gslides, .gsheet), which is a small JSON pointer holding the file id.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .readers import read_source

_EXPORTS = {
    "application/vnd.google-apps.document":
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx", "google_doc"),
    "application/vnd.google-apps.presentation":
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx", "google_slides"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv", "google_sheet"),
}
SHORTCUT_EXTENSIONS = frozenset({".gdoc", ".gslides", ".gsheet"})

_URL_ID = re.compile(r"/(?:document|presentation|spreadsheets|file)/d/([A-Za-z0-9_-]{10,})|[?&]id=([A-Za-z0-9_-]{10,})")
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def file_id(ref: str) -> str:
    """File id from a Google URL, a bare id, or a .gdoc/.gslides/.gsheet shortcut path."""
    ref = ref.strip()
    p = Path(ref)
    if p.suffix.lower() in SHORTCUT_EXTENSIONS and p.exists():
        data = json.loads(p.read_text())
        fid = data.get("doc_id") or (data.get("resource_id") or "").split(":")[-1]
        if not fid and data.get("url"):
            return file_id(data["url"])
        if not fid:
            raise ValueError(f"no file id in shortcut {p.name}")
        return fid
    m = _URL_ID.search(ref)
    if m:
        return m.group(1) or m.group(2)
    if _BARE_ID.match(ref):
        return ref
    raise ValueError(f"not a Google file URL, id, or shortcut: {ref!r}")


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip() or "untitled"


def fetch(drive, ref: str, dest: Path) -> dict:
    """Export one Google file into dest and read it. Returns the reader's document dict
    with doc_type set to google_doc / google_slides / google_sheet and a "google" block
    of provenance (file id, title, mime type, modified time)."""
    fid = file_id(ref)
    meta = drive.files().get(fileId=fid, fields="id,name,mimeType,modifiedTime",
                             supportsAllDrives=True).execute()
    export = _EXPORTS.get(meta["mimeType"])
    if export is None:
        raise ValueError(f"{meta['name']!r} is {meta['mimeType']}, not a Google Doc, Slides deck, or Sheet")
    mime, ext, doc_type = export
    data = drive.files().export(fileId=fid, mimeType=mime).execute()
    dest.mkdir(parents=True, exist_ok=True)
    local = dest / f"{_safe_name(meta['name'])}_{fid[:8]}{ext}"
    local.write_bytes(data if isinstance(data, bytes) else data.encode())
    doc = read_source(local)
    doc["filename"] = meta["name"]
    doc["doc_type"] = doc_type
    doc["google"] = {"file_id": fid, "title": meta["name"], "mime_type": meta["mimeType"],
                     "modified_time": meta.get("modifiedTime", "")}
    return doc
