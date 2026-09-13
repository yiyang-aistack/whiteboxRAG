#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
whiteBoxRAG demo seeder.

Creates (or reuses) a demo knowledge base, uploads a sample document, waits for the
asynchronous ingestion task to finish, then asks one question and prints the traced
answer: the answer text, a per-sentence citation verdict and the evaluation metrics.

The script talks to the REST API only, so the service must already be running:

    python main.py --install          # or: docker compose up -d
    python scripts/seed_demo.py

Exit code 0 on success, 1 on failure.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:  # pragma: no cover - httpx is declared in requirements.txt
    print("[FAIL] httpx is required, run: pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOC = ROOT / "examples" / "demo" / "whiteBoxRAG_FAQ.txt"
DEFAULT_QUERY = "混合检索里 BM25 的默认权重是多少？"
DEFAULT_KB_NAME = "whiteBoxRAG Demo KB"

# confidence_level reported by core/sentence_tracing.py -> (marker, readable label)
VERDICTS = {
    "direct_quote": ("[OK]  ", "direct evidence in the retrieved chunks"),
    "summary": ("[OK]  ", "faithful summary of the retrieved chunks"),
    "low_confidence": ("[WARN]", "low confidence, no close match"),
    "drift": ("[BAD] ", "unsupported drift (possible hallucination)"),
    "no_source": ("[BAD] ", "no source found"),
}


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def info(msg: str) -> None:
    print(f"    {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"\n[FAIL] {msg}", file=sys.stderr, flush=True)


def wait_for_server(client: httpx.Client, base_url: str, timeout: int) -> bool:
    """Poll /api/health until the service answers or the timeout expires."""
    step(f"Waiting for the API at {base_url} (up to {timeout}s)")
    deadline = time.time() + timeout
    last_error = "no response yet"
    while time.time() < deadline:
        try:
            response = client.get(f"{base_url}/api/health")
            if response.status_code == 200:
                info(f"API is up (status={response.json().get('status', 'ok')})")
                return True
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # connection refused while the server boots
            last_error = exc.__class__.__name__
        time.sleep(2)
    fail(f"API not reachable within {timeout}s ({last_error}).")
    print("       Start it with 'python main.py --install' or 'docker compose up -d'.", file=sys.stderr)
    return False


def resolve_kb(client: httpx.Client, base_url: str, name: str) -> str:
    """Return the kb_id for `name`, creating the knowledge base when needed."""
    step(f"Resolving knowledge base '{name}'")
    response = client.get(f"{base_url}/api/knowledge/list")
    response.raise_for_status()
    for kb in response.json().get("data", []):
        if kb.get("name") == name:
            info(f"reusing existing knowledge base: {kb['kb_id']}")
            return kb["kb_id"]

    response = client.post(
        f"{base_url}/api/knowledge/create",
        json={"name": name, "description": "Seeded by scripts/seed_demo.py"},
    )
    if response.status_code >= 400:
        fail(f"could not create the knowledge base: HTTP {response.status_code} {response.text[:200]}")
        sys.exit(1)
    kb_id = response.json()["kb_id"]
    info(f"created knowledge base: {kb_id}")
    return kb_id


def upload_document(client: httpx.Client, base_url: str, kb_id: str, doc: Path) -> None:
    """Upload the demo document once and wait for the ingestion task to complete."""
    step(f"Uploading {doc.name}")
    if not doc.is_file():
        fail(f"demo document not found: {doc}")
        sys.exit(1)

    response = client.get(f"{base_url}/api/knowledge/{kb_id}/files")
    if response.status_code == 200 and response.json().get("total", 0) > 0:
        info("document already ingested, skipping upload")
        return

    with doc.open("rb") as handle:
        response = client.post(
            f"{base_url}/api/knowledge/{kb_id}/upload",
            files={"file": (doc.name, handle, "text/plain")},
        )
    if response.status_code >= 400:
        fail(f"upload failed: HTTP {response.status_code} {response.text[:200]}")
        sys.exit(1)

    task_id = response.json().get("task_id")
    info(f"ingestion task started: {task_id}")

    step("Waiting for parsing and embedding (the first run also loads the embedding model)")
    deadline = time.time() + 600
    last_message = ""
    while time.time() < deadline:
        try:
            status = client.get(f"{base_url}/api/knowledge/{kb_id}/status")
            data = status.json().get("data", {}) if status.status_code == 200 else {}
            task = next((t for t in data.get("recent_tasks", []) if t.get("task_id") == task_id), None)
            if task:
                if task.get("message") and task["message"] != last_message:
                    last_message = task["message"]
                    info(f"{task.get('status')} {task.get('progress')}% - {last_message}")
                if task.get("status") == "completed":
                    info(f"ingested {data.get('chunk_count')} chunks")
                    return
                if task.get("status") == "failed":
                    fail(f"ingestion failed: {task.get('message')}")
                    sys.exit(1)
        except Exception as exc:  # transient polling error, keep waiting
            info(f"status poll failed ({exc.__class__.__name__}), retrying")
        time.sleep(2)

    fail("ingestion did not finish within 600s")
    sys.exit(1)


def ask(client: httpx.Client, base_url: str, kb_id: str, query: str) -> dict:
    """Ask one question in non-streaming mode so the full trace comes back in one payload."""
    step(f"Asking: {query}")
    response = client.post(
        f"{base_url}/api/chat/stream",
        json={"kb_id": kb_id, "query": query, "stream": False},
        headers={"Accept-Language": "zh-CN"},
    )
    if response.status_code >= 400:
        fail(f"chat request failed: HTTP {response.status_code} {response.text[:300]}")
        sys.exit(1)
    return response.json()


def report(payload: dict) -> None:
    """Print the answer, the retrieval evidence and the white-box trace."""
    print("\n" + "=" * 78)
    print("ANSWER")
    print("=" * 78)
    print(payload.get("answer", "<empty answer>"))

    print("\n" + "-" * 78)
    print("RETRIEVAL")
    print("-" * 78)
    print(f"  mode={payload.get('retrieval_mode')}  has_results={payload.get('has_results')}  "
          f"chunks={len(payload.get('context') or [])}  duration={payload.get('duration')}s")
    for rank, chunk in enumerate((payload.get("context") or [])[:3], start=1):
        score = chunk.get("score")
        text = (chunk.get("content") or chunk.get("text") or "").replace("\n", " ")
        source = (chunk.get("metadata") or {}).get("file_name", "?")
        pretty_score = "?" if score is None else round(float(score), 3)
        print(f"  [{rank}] score={pretty_score} source={source}")
        print(f"      {text[:110]}...")

    print("\n" + "-" * 78)
    print("SENTENCE-LEVEL TRACING (the white-box part)")
    print("-" * 78)
    traced = payload.get("sentence_tracing") or []
    if not traced:
        print("  (no tracing data returned)")
    for item in traced:
        marker, label = VERDICTS.get(
            item.get("confidence_level"),
            ("[?]   ", item.get("confidence_level") or "unknown"),
        )
        print(f"  {marker} {label}")
        print(f"        {item.get('sentence', '')[:100]}")

    drift = payload.get("drift_analysis") or {}
    if drift:
        print(f"  sentences={drift.get('total_sentences')}  direct_quote={drift.get('direct_quote_count')}  "
              f"summary={drift.get('summary_count')}  drift={drift.get('drift_count')}  "
              f"drift_rate={drift.get('drift_rate')}")

    evaluation = payload.get("evaluation") or {}
    if evaluation:
        print("\n" + "-" * 78)
        print("EVALUATION")
        print("-" * 78)
        print(f"  overall_score={evaluation.get('overall_score')}  is_passing={evaluation.get('is_passing')}")
        for name, metric in (evaluation.get("metrics") or {}).items():
            print(f"  {name}={metric.get('value')} (target={metric.get('target')}, pass={metric.get('is_pass')})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed a demo knowledge base and ask one traced question.")
    parser.add_argument("--base-url", default="http://localhost:8080", help="API base URL")
    parser.add_argument("--kb-name", default=DEFAULT_KB_NAME, help="demo knowledge base name")
    parser.add_argument("--file", default=str(DEFAULT_DOC),
                        help="document to ingest (.txt/.pdf/.docx/.xlsx/.pptx)")
    parser.add_argument("--query", default=DEFAULT_QUERY, help="question to ask")
    parser.add_argument("--wait", type=int, default=60, help="seconds to wait for the API to come up")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    print("whiteBoxRAG demo seeder")
    print(f"  API      : {base_url}")
    print(f"  document : {args.file}")
    print(f"  question : {args.query}")

    with httpx.Client(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
        if not wait_for_server(client, base_url, args.wait):
            return 1
        kb_id = resolve_kb(client, base_url, args.kb_name)
        upload_document(client, base_url, kb_id, Path(args.file))
        payload = ask(client, base_url, kb_id, args.query)
        report(payload)
        trace_id = payload.get("trace_id")

    print("\n" + "=" * 78)
    print(f"Open {base_url}/ and select the knowledge base '{args.kb_name}' to see the same trace in the UI.")
    if trace_id:
        print(f"Trace id: {trace_id}  (GET {base_url}/api/chat/trace/{trace_id})")
    print("Traces are also persisted under storage/traces/.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
