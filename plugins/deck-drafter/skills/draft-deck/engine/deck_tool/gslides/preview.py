"""
Slide images for visual checks and rendered-output QA. Two routes:

- Google: upload a .pptx as a Google Slides file (Google converts it) and fetch each
  slide's thumbnail. Needs the network and the drive.file scope. Google's conversion
  flattens native PowerPoint charts to pictures, which is fine for looking at a slide.
- LibreOffice: convert the .pptx to PDF headless, then rasterise pages with pdftoppm
  or LibreOffice's own PNG export. Local, no network.

Uploaded files are titled "[deck-tool] preview: <name>" and listed in the return value
so a run report can say what was created in Drive.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
SLIDES_MIME = "application/vnd.google-apps.presentation"


def google_thumbnails(pptx_path, out_dir, size: str = "LARGE", file_id: str | None = None) -> dict:
    """Returns {"file_id", "url", "images": [paths]}. Pass file_id to reuse a deck
    already uploaded instead of creating another."""
    from google.auth.transport.requests import AuthorizedSession
    from googleapiclient.http import MediaFileUpload

    from .auth import credentials, drive_service, slides_service

    pptx_path, out_dir = Path(pptx_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if file_id is None:
        drive = drive_service()
        file_id = drive.files().create(
            body={"name": f"[deck-tool] preview: {pptx_path.stem}", "mimeType": SLIDES_MIME},
            media_body=MediaFileUpload(str(pptx_path), mimetype=PPTX_MIME), fields="id").execute()["id"]
    f = {"id": file_id}
    http = AuthorizedSession(credentials())
    slides = slides_service()
    pres = slides.presentations().get(presentationId=f["id"], fields="slides.objectId").execute()
    images = []
    for i, s in enumerate(pres.get("slides", []), start=1):
        thumb = slides.presentations().pages().getThumbnail(
            presentationId=f["id"], pageObjectId=s["objectId"],
            thumbnailProperties_thumbnailSize=size).execute()
        p = out_dir / f"{pptx_path.stem}_{i:02d}.png"
        r = http.get(thumb["contentUrl"], timeout=60)
        r.raise_for_status()
        p.write_bytes(r.content)
        images.append(p)
    return {"file_id": f["id"], "url": f"https://docs.google.com/presentation/d/{f['id']}/edit", "images": images}


def soffice_path() -> str | None:
    for cand in (shutil.which("soffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if cand and Path(cand).exists():
            return cand
    return None


def libreoffice_images(pptx_path, out_dir, dpi: int = 80) -> list[Path]:
    """PDF via LibreOffice, then one PNG per page (pdftoppm if present, else pypdf-free
    fallback through LibreOffice's PNG export of the first page only)."""
    soffice = soffice_path()
    if not soffice:
        raise RuntimeError("LibreOffice is not installed")
    pptx_path, out_dir = Path(pptx_path), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx_path)],
                   check=True, capture_output=True, timeout=300)
    pdf = out_dir / f"{pptx_path.stem}.pdf"
    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        subprocess.run([pdftoppm, "-png", "-r", str(dpi), str(pdf), str(out_dir / pptx_path.stem)],
                       check=True, capture_output=True, timeout=300)
        return sorted(out_dir.glob(f"{pptx_path.stem}-*.png"))
    subprocess.run([soffice, "--headless", "--convert-to", "png", "--outdir", str(out_dir), str(pptx_path)],
                   check=True, capture_output=True, timeout=300)
    return sorted(out_dir.glob(f"{pptx_path.stem}*.png"))
