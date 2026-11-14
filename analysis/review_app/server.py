"""HW4 review server: conversations, notes, taxonomy, labels, suggestions.

Adapted from the reference server in ``analysis/server.py``: a standard-library
HTTP server over plain JSON files in ``analysis/state/``. What differs:

- It serves whole conversations (all turns of one scenario) joined with the
  scenario tuple, the expected outcome, and the seed-DB record facts.
- Binary labels have their own endpoint. Each judgment is appended to
  ``state/labels/<mode>.jsonl`` (a flip marks the prior row ``superseded_by``)
  and written to Langfuse as a score on the conversation id.
- Suggestions are never deleted. Accepting or rejecting one records the
  decision and the reviewer's reason in ``state/suggestions.json``.

API:

    GET  /                     the review app (index.html beside this file)
    GET  /api/conversations    all conversations in the review pool
    GET  /api/info             trace source, counts, Langfuse status
    GET|POST /api/manifest     state/sample_manifest.json (batches)
    GET|POST /api/annotations  state/annotations.json (free-text notes)
    GET|POST /api/patterns     state/patterns.json (taxonomy)
    GET|POST /api/suggestions  state/suggestions.json (AI suggestions + decisions)
    GET  /api/labels           live label per mode and conversation
    POST /api/labels           {mode, trace_id, label 0|1, evidence?}
    POST /api/labels/bulk      {labels: [{mode, trace_id, label, evidence?, scenario_id?, source?}]}

Run from the repository root:

    uv run python -m analysis.review_app.server                       # Langfuse
    uv run python -m analysis.review_app.server --source traces/support_traces.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from analysis.helpers import _state, langfuse_io
from analysis.review_app import data

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# API path -> (state file, default document).
STATE_FILES: dict[str, tuple[str, Any]] = {
    "/api/manifest": ("sample_manifest.json", {"batches": []}),
    "/api/annotations": ("annotations.json", {"annotations": []}),
    "/api/patterns": ("patterns.json", {"modes": []}),
    "/api/suggestions": ("suggestions.json", []),
    "/api/proposals": ("label_proposals.json", []),
}

# Filled in by main() (or by tests): the conversation pool and source info.
POOL: dict[str, Any] = {"conversations": [], "info": {}, "langfuse": False}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------


def live_labels() -> dict[str, dict[str, dict[str, Any]]]:
    """Return ``{mode: {trace_id: row}}`` for the live (not superseded) rows."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    labels_dir = _state.state_path("labels")
    if not labels_dir.exists():
        return out
    for path in sorted(labels_dir.glob("*.jsonl")):
        live: dict[str, dict[str, Any]] = {}
        for row in _state.read_jsonl(path):
            if not row.get("superseded_by"):
                live[row["trace_id"]] = row
        out[path.stem] = live
    return out


def save_label(mode: str, trace_id: str, label: int, evidence: str | None,
               source: str = "human", scenario_id: str | None = None) -> dict[str, Any]:
    """Record one judgment append-only; return the live row.

    A repeat of the live label with the same evidence is a no-op. A change
    appends a new row and marks the prior row ``superseded_by`` so the flip
    history stays in the file.
    """
    if label not in (0, 1):
        raise ValueError("label must be 0 (failure absent) or 1 (failure present)")
    path = _state.state_path("labels", f"{mode}.jsonl")
    rows = _state.read_jsonl(path)
    prior = [r for r in rows if r["trace_id"] == trace_id]
    live = next((r for r in reversed(prior) if not r.get("superseded_by")), None)
    if live and live["label"] == label and (live.get("evidence") or None) == (evidence or None):
        return live
    row = {
        "trace_id": trace_id,
        "label": label,
        "source": source,
        "ts": _utcnow(),
        "label_id": f"{trace_id}#{len(prior)}",
        "scenario_id": scenario_id,
        "evidence": evidence or None,
    }
    if live:
        for r in rows:
            if r is live:
                r["superseded_by"] = row["label_id"]
        _state.write_jsonl(path, rows)
    _state.append_jsonl(path, row)
    return row


def write_score(mode: str, trace_id: str, label: int, evidence: str | None, flush: bool = True) -> bool:
    """Write the judgment to Langfuse as a numeric score; False when offline.

    The score id is fixed per (conversation, mode), so a flip updates the
    score instead of adding a second one.
    """
    if not POOL["langfuse"]:
        return False
    client = langfuse_io._client()
    client.create_score(
        name=mode,
        value=int(label),
        trace_id=trace_id,
        score_id=f"hw4-{trace_id}-{mode}",
        data_type="NUMERIC",
        comment=evidence,
    )
    if flush:
        client.flush()
    return True


def save_labels_bulk(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Save many judgments (each as save_label would) and write their scores with one flush."""
    rows, scored, errors = [], 0, []
    for it in items:
        mode, trace_id, label = str(it["mode"]), str(it["trace_id"]), int(it["label"])
        evidence = it.get("evidence") or None
        rows.append(save_label(mode, trace_id, label, evidence, source=it.get("source", "human"),
                               scenario_id=it.get("scenario_id")))
        try:
            scored += write_score(mode, trace_id, label, evidence, flush=False)
        except Exception as exc:  # pragma: no cover - network path
            errors.append(f"{mode}/{trace_id}: {exc}")
    if scored:
        langfuse_io._client().flush()
    return {"ok": not errors, "saved": len(rows), "langfuse_scores": scored, "errors": errors[:5]}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class ReviewHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        return

    def _send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return None

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")  # a stale page writes old-format records
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/conversations":
            self._send_json(POOL["conversations"])
        elif path == "/api/info":
            self._send_json({**POOL["info"], "langfuse_scores": POOL["langfuse"]})
        elif path == "/api/labels":
            self._send_json(live_labels())
        elif path in STATE_FILES:
            name, default = STATE_FILES[path]
            self._send_json(_state.read_json(_state.state_path(name), default=default))
        else:
            self._send_json({"error": f"unknown path: {path}"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        payload = self._read_body()
        if payload is None:
            self._send_json({"error": "expected a JSON body"}, status=400)
            return
        if path == "/api/labels":
            self._post_label(payload)
        elif path == "/api/labels/bulk":
            try:
                items = list(payload["labels"])
                for it in items:
                    if int(it["label"]) not in (0, 1) or not it.get("mode") or not it.get("trace_id"):
                        raise ValueError(f"bad label item: {it}")
            except (KeyError, TypeError, ValueError) as exc:
                self._send_json({"error": f"need labels: [{{mode, trace_id, label 0|1}}] ({exc})"}, status=400)
                return
            self._send_json(save_labels_bulk(items))
        elif path in STATE_FILES:
            name, _ = STATE_FILES[path]
            _state.write_json(_state.state_path(name), payload)
            self._send_json({"ok": True})
        else:
            self._send_json({"error": f"cannot POST to {path}"}, status=404)

    def _post_label(self, payload: dict[str, Any]) -> None:
        try:
            mode = str(payload["mode"])
            trace_id = str(payload["trace_id"])
            label = int(payload["label"])
        except (KeyError, TypeError, ValueError):
            self._send_json({"error": "need mode, trace_id, label"}, status=400)
            return
        evidence = payload.get("evidence") or None
        try:
            row = save_label(mode, trace_id, label, evidence,
                             source=payload.get("source", "human"),
                             scenario_id=payload.get("scenario_id"))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        try:
            scored = write_score(mode, trace_id, label, evidence)
        except Exception as exc:  # pragma: no cover - network path
            self._send_json({"row": row, "error": f"Langfuse score write failed: {exc}",
                             "cached_locally": True}, status=502)
            return
        self._send_json({"ok": True, "row": row, "langfuse_score": scored})


# ---------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------


def load_pool(source: str, scenarios: Path, db: Path | None, project: str) -> None:
    """Build the conversation pool from Langfuse or an export file."""
    scen = data.load_scenarios(scenarios)
    if source == "langfuse":
        if not langfuse_io.is_configured():
            sys.exit("Langfuse is not configured; pass --source traces/support_traces.json")
        print("fetching traces from Langfuse (one request per trace, about a minute)…")
        traces = data.traces_from_langfuse()
    else:
        traces = data.traces_from_export(Path(source))
    convs = data.build_conversations(traces, scen, data.facts_reader(db), project=project)
    POOL["conversations"] = convs
    POOL["info"] = {
        "source": source,
        "scenarios": str(scenarios),
        "db": str(db) if db else None,
        "conversations": len(convs),
        "traces": sum(len(c["turns"]) for c in convs),
        "missing_scenarios": sorted(set(scen) - {c["scenario_id"] for c in convs}),
        "loaded_at": _utcnow(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="langfuse",
                        help='"langfuse" (default) or a Module 1 export such as traces/support_traces.json')
    parser.add_argument("--scenarios", type=Path, default=REPO / "scenarios/support_scenarios.jsonl")
    parser.add_argument("--db", type=Path, default=REPO / "data/review_seed.db",
                        help="fresh seed copy for the record panel (not the DB the run changed)")
    parser.add_argument("--project", default=data.DEFAULT_PROJECT, help="Langfuse project id for trace links")
    parser.add_argument("--offline", action="store_true", help="do not write Langfuse scores")
    parser.add_argument("--port", type=int, default=8021)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv

        load_dotenv(REPO / ".env")
    except ImportError:
        pass

    db = args.db if args.db and args.db.exists() else None
    if args.db and db is None:
        print(f"no seed copy at {args.db}; the record panel stays empty "
              "(create it with: uv run python -m seed.generate --db data/review_seed.db --policies <tmp dir>)")
    load_pool(args.source, args.scenarios, db, args.project)
    POOL["langfuse"] = not args.offline and langfuse_io.is_configured()
    info = POOL["info"]
    print(f"{info['conversations']} conversations / {info['traces']} traces from {args.source}; "
          f"missing scenarios: {len(info['missing_scenarios'])}; "
          f"Langfuse scores: {'on' if POOL['langfuse'] else 'off'}")

    server = ThreadingHTTPServer((args.host, args.port), ReviewHandler)
    print(f"review app on http://{args.host}:{args.port}/  (state: {_state.state_root()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
