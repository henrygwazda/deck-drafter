"""
Google OAuth for the deck tool (installed-app flow, runs on the user's own machine).

Setup is in docs/GOOGLE_SETUP.md. Credentials live outside the repo:
    ~/.config/deck-tool/client_secret.json   the OAuth client downloaded from Google Cloud Console
    ~/.config/deck-tool/token.json           written on first sign-in

Scopes: drive.file (write access only to files the app created or the user opened with
it) plus drive.readonly. drive.readonly was added on 2026-10-02 at Henry's direction so
ingestion can read existing Google Docs and Slides decks as source material by URL or
ID. It is read-only: the tool still cannot modify or delete any file it did not create.
Override with DECK_TOOL_SCOPES (comma-separated) to narrow for a given run. If the stored
token was granted fewer scopes than requested, credentials() re-runs sign-in.

Re-consent after a scope change: python3 -m deck_tool.gslides.auth
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("DECK_TOOL_CONFIG", Path.home() / ".config" / "deck-tool"))
DEFAULT_SCOPES = ["https://www.googleapis.com/auth/drive.file",
                  "https://www.googleapis.com/auth/drive.readonly"]


def scopes():
    raw = os.environ.get("DECK_TOOL_SCOPES")
    return [s.strip() for s in raw.split(",") if s.strip()] if raw else DEFAULT_SCOPES


def _granted(token_path: Path) -> set[str]:
    import json
    try:
        return set(json.loads(token_path.read_text()).get("scopes") or [])
    except (OSError, ValueError):
        return set()


def credentials(force_signin: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path, secret_path = CONFIG / "token.json", CONFIG / "client_secret.json"
    creds = None
    if token_path.exists() and not force_signin and set(scopes()) <= _granted(token_path):
        creds = Credentials.from_authorized_user_file(str(token_path), scopes())
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not secret_path.exists():
            raise SystemExit(f"Missing {secret_path}. Follow docs/GOOGLE_SETUP.md first.")
        creds = InstalledAppFlow.from_client_secrets_file(str(secret_path), scopes()).run_local_server(port=0)
    CONFIG.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    token_path.chmod(0o600)
    return creds


def slides_service():
    from googleapiclient.discovery import build
    return build("slides", "v1", credentials=credentials(), cache_discovery=False)


def drive_service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=credentials(), cache_discovery=False)


if __name__ == "__main__":
    c = credentials(force_signin="--force" in sys.argv)
    print("signed in with scopes:", ", ".join(sorted(c.scopes or scopes())))
