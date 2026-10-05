"""Source registry: assigns stable SRC ids in input order."""
from __future__ import annotations


def build_sources(raw_documents: list[dict]) -> list[dict]:
    sources = []
    for i, doc in enumerate(raw_documents):
        src = {"id": f"SRC{i + 1:02d}", "filename": doc["filename"], "doc_type": doc["doc_type"], "note": ""}
        if doc.get("google"):
            src["google"] = doc["google"]
        sources.append(src)
    return sources
