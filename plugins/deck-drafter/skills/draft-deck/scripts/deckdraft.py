#!/usr/bin/env python3
"""
deck-drafter entry point. Runs the deck tool's engine with no model calls: Claude, in the
conversation, makes the judgment calls; this script reads sources, checks the draft
against them, and builds the deck.

  deckdraft.py doctor                                    check Python packages, set them up if missing
  deckdraft.py intake <project> [inputs ...] [--google URL ...]
                                                         register and read sources -> intake.json, sources_text/
  deckdraft.py build <project>                           draft.json -> out/<deck>.pptx, .html, REVIEW.md
  deckdraft.py slides <project>                          push the built deck to native Google Slides
                                                         (needs the engine's OAuth files in ~/.config/deck-tool)

Every command prints one JSON object on stdout.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
REQUIRED = {"pptx": "python-pptx>=1.0", "docx": "python-docx>=1.1", "pypdf": "pypdf>=4.0",
            "jsonschema": "jsonschema>=4.18", "openpyxl": "openpyxl>=3.1"}
OPTIONAL = {"googleapiclient": "google-api-python-client>=2.100", "google_auth_oauthlib": "google-auth-oauthlib>=1.2"}


def engine_root() -> Path:
    """The repository when run from a checkout (so edits take effect without repackaging),
    otherwise the engine vendored into the installed plugin by build_plugin.py."""
    if os.environ.get("DECK_TOOL_ROOT"):
        return Path(os.environ["DECK_TOOL_ROOT"])
    for p in HERE.parents:
        if (p / "deck_tool").is_dir() and (p / "template" / "layout_manifest.json").exists() and (p / ".git").exists():
            return p
    vendored = HERE.parent / "engine"
    if (vendored / "deck_tool").is_dir():
        return vendored
    sys.exit(json.dumps({"ok": False, "error": "deck tool engine not found next to the plugin or in a parent folder"}))


def data_dir() -> Path:
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.deck-drafter")
    return Path(base)


def _missing(mods) -> list[str]:
    out = []
    for m in mods:
        try:
            __import__(m)
        except ImportError:
            out.append(m)
    return out


def in_sandbox() -> bool:
    """True in a throwaway code-execution sandbox (Claude chat, Cowork's VM, Claude Code
    on the web), where installing into the running Python is quick and harmless. On a
    person's own machine the packages go into a private environment instead."""
    return (Path("/mnt/user-data").exists() or bool(os.environ.get("CLAUDE_CODE_REMOTE"))
            or bool(os.environ.get("DECKDRAFT_SANDBOX")))


def _pip(py: str, extra: list[str], pkgs: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([py, "-m", "pip", "install", "-q", *extra, *pkgs], capture_output=True, text=True)


def _install_here(missing: list[str]) -> bool:
    pkgs = [REQUIRED[m] for m in missing]
    for extra in ([], ["--user"], ["--break-system-packages"], ["--user", "--break-system-packages"]):
        if _pip(sys.executable, extra, pkgs).returncode == 0:
            return True
    return False


def _install_venv() -> Path | None:
    env_dir = data_dir() / "venv"
    py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    try:
        if not py.exists():
            env_dir.parent.mkdir(parents=True, exist_ok=True)
            venv.EnvBuilder(with_pip=True).create(env_dir)
        check = subprocess.run([str(py), "-c", "import " + ", ".join(REQUIRED)], capture_output=True)
        if check.returncode != 0 and _pip(str(py), [], [*REQUIRED.values(), *OPTIONAL.values()]).returncode != 0:
            return None
    except Exception:
        return None
    return py


def ensure_packages():
    """Make the engine's packages importable, then carry on (re-running this script under
    another Python if that is where they were installed)."""
    missing = _missing(REQUIRED)
    if not missing:
        return
    if os.environ.get("DECKDRAFT_SETUP_DONE"):
        sys.exit(json.dumps({"ok": False, "error": f"packages still missing after setup: {missing}"}))
    os.environ["DECKDRAFT_SETUP_DONE"] = "1"
    order = ("here", "venv") if in_sandbox() else ("venv", "here")
    for how in order:
        if how == "here" and _install_here(missing):
            os.execv(sys.executable, [sys.executable, __file__, *sys.argv[1:]])
        if how == "venv":
            py = _install_venv()
            if py:
                os.execv(str(py), [str(py), __file__, *sys.argv[1:]])
    sys.exit(json.dumps({"ok": False, "error": "could not install the Python packages the engine needs",
                         "missing": missing, "fix": f"pip install {' '.join(REQUIRED.values())}",
                         "note": "In Claude chat this needs code execution with access to package managers "
                                 "(Settings > Capabilities)."}))


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    ensure_packages()
    root = engine_root()
    sys.path.insert(0, str(root))

    cmd = args[0]
    if cmd == "doctor":
        from deck_tool.draft.intake import soffice_path
        print(json.dumps({"ok": True, "python": sys.executable, "engine": str(root), "sandbox": in_sandbox(),
                          "libreoffice": soffice_path(), "google_libs": not _missing(OPTIONAL),
                          "google_oauth": (Path.home() / ".config" / "deck-tool").is_dir()}, indent=1))
    elif cmd == "intake" and len(args) > 1:
        from deck_tool.draft.intake import intake
        google = [args[i + 1] for i, a in enumerate(args) if a == "--google" and i + 1 < len(args)]
        inputs = [a for i, a in enumerate(args[2:], start=2) if a != "--google" and args[i - 1] != "--google"]
        out = intake(args[1], inputs, google)
        srcs = out["sources"]
        print(json.dumps({"ok": True, "report": str(Path(args[1]) / "intake_report.md"),
                          "read": [f"{s['id']} {s['filename']} ({s['doc_type']}, {s.get('lines', 0)} lines)"
                                   for s in srcs if s["status"] == "read"],
                          "needs_attention": [f"{s['id']} {s['filename']}: {s['status']}: {s['note']}"
                                              for s in srcs if s["status"] != "read"],
                          "not_found": out.get("not_found", [])}, indent=1))
    elif cmd == "build" and len(args) > 1:
        from deck_tool.draft.build import DraftError, build
        try:
            res = build(args[1])
        except DraftError as e:
            print(json.dumps({"ok": False, "fix_these": e.errors}, indent=1))
            sys.exit(2)
        print(json.dumps(dict(ok=True, **res), indent=1))
    elif cmd == "slides" and len(args) > 1:
        from deck_tool.gslides import deck as gdeck
        out = Path(args[1]).resolve() / "out"
        gdeck.run_dir = lambda _deck_id: out  # keep the slide map with the draft, not in the engine's runs/
        snap = gdeck.create_deck(str(out / "deck_spec.json"))
        print(json.dumps({"ok": True, "url": f"https://docs.google.com/presentation/d/{snap['presentation_id']}/edit",
                          "pages": len(snap["slide_order"])}, indent=1))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
