from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
# DECK_TOOL_RUNS moves run folders out of the repo, which the plugin needs: its engine
# copy may sit in a read-only plugin cache, and a user's drafts belong in their workspace.
RUNS = Path(os.environ["DECK_TOOL_RUNS"]) if os.environ.get("DECK_TOOL_RUNS") else ROOT / "runs"

LIMIT_PATTERN = re.compile(r"usage limit|session limit|rate limit|limit reached|limit will reset|try again (?:at|in)", re.I)
CLAUDE_TIMEOUT = 900
CLAUDE_EXTRA_ARGS: list[str] = []


class UsageLimit(Exception):
    pass


def load_json(path, default=None):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:16]


def fill(name: str, **vals) -> str:
    text = (PROMPTS / name).read_text()
    for k, v in vals.items():
        text = text.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v, indent=1, ensure_ascii=False))
    return text


def llm_mode() -> str:
    """DECK_TOOL_LLM=claude (default) calls the Claude Code CLI. =stub uses offline heuristics for testing."""
    return os.environ.get("DECK_TOOL_LLM", "claude")


def run_claude(prompt: str, model: str = "sonnet", schema: dict | None = None) -> str:
    """Returns the model's text, or with a schema, the JSON text of the CLI's
    schema-validated structured_output."""
    cmd = ["claude", "-p", "--model", model, *CLAUDE_EXTRA_ARGS]
    if schema is not None:
        cmd += ["--output-format", "json", "--json-schema", json.dumps(schema)]
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("claude call timed out")
    out, err = r.stdout.strip(), r.stderr.strip()
    if r.returncode != 0 and schema is not None and out.startswith("{"):
        # With --output-format json a failed call still returns an envelope; report its
        # error fields rather than the first 500 characters of usage metadata.
        try:
            env = json.loads(out)
        except json.JSONDecodeError:
            env = {}
        detail = {k: env.get(k) for k in ("subtype", "terminal_reason", "api_error_status", "result", "errors")
                  if env.get(k) not in (None, "", [])}
        if LIMIT_PATTERN.search(str(detail)):
            raise UsageLimit(str(detail))
        raise RuntimeError(f"claude exited {r.returncode}: {json.dumps(detail)[:1500]}")
    if r.returncode != 0:
        if LIMIT_PATTERN.search(out + " " + err):
            raise UsageLimit(out or err)
        raise RuntimeError(f"claude exited {r.returncode}: {(err or out)[:500]}")
    if schema is not None:
        try:
            envelope = json.loads(out)
        except json.JSONDecodeError:
            raise RuntimeError(f"claude --output-format json returned non-JSON: {out[:300]}")
        result = str(envelope.get("result", ""))
        if envelope.get("is_error"):
            if LIMIT_PATTERN.search(result):
                raise UsageLimit(result)
            raise RuntimeError(f"claude reported an error: {result[:500]}")
        if "structured_output" not in envelope:
            raise ValueError("no structured_output in CLI response")
        return json.dumps(envelope["structured_output"])
    if len(out) < 400 and LIMIT_PATTERN.search(out):
        raise UsageLimit(out)
    return out


def extract_json(out: str):
    """Tolerates markdown fences and prose around a JSON object (the stage 2 EVD fix).
    When the text holds more than one top-level object -- a real failure mode is the
    model printing a malformed object, a "Correction:" line, then a valid one -- the
    last object that parses wins, since that is the one the model settled on."""
    out = re.sub(r"^```\w*\s*$", "", out.strip(), flags=re.M)
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in output")
    try:
        return json.loads(out[start:end + 1])
    except json.JSONDecodeError as first_error:
        decoder, found, i = json.JSONDecoder(), None, start
        while i >= 0:
            try:
                obj, i_end = decoder.raw_decode(out, i)
                if isinstance(obj, dict):
                    found = obj
                i = out.find("{", i_end)
            except json.JSONDecodeError:
                i = out.find("\n{", i + 1)
                i = i + 1 if i >= 0 else -1
        if found is None:
            raise first_error
        return found


def _save_failure(prompt: str, out: str, attempt: int, err) -> Path:
    """Keep the raw unparseable output so a JSON failure can be diagnosed after the fact."""
    d = RUNS / "_llm_failures"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sha(prompt)}_{attempt}.txt"
    p.write_text(f"# error: {err}\n{out}")
    return p


def call_json(prompt: str, model: str = "sonnet", schema: dict | None = None):
    """schema, when given, makes the CLI enforce it (--json-schema), which rules out
    malformed or doubled JSON rather than recovering from it afterwards."""
    last, saved = None, []
    for attempt in range(2):
        out = run_claude(prompt, model, schema)
        try:
            return extract_json(out)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            saved.append(str(_save_failure(prompt, out, attempt, e)))
    raise RuntimeError(f"model returned unparseable JSON twice: {last} (raw output: {', '.join(saved)})")


def run_dir(deck_id: str) -> Path:
    d = RUNS / deck_id
    d.mkdir(parents=True, exist_ok=True)
    return d
