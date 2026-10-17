"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .api import APIConfig, ModelClient
from .pipeline import (
    apply_phase2,
    build_context,
    materialize_phase1,
    result_as_json,
    run_phase1,
    run_phase2,
)
from .storage import MemoryStore


def _config(args: argparse.Namespace) -> APIConfig:
    file_config = {}
    if args.config:
        file_config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    api_key = args.api_key or os.getenv("CODEX_MEMORY_API_KEY") or os.getenv("OPENAI_API_KEY") or file_config.get("api_key", "")
    base_url = args.base_url or os.getenv("CODEX_MEMORY_BASE_URL") or file_config.get("base_url", "https://api.openai.com/v1")
    model = args.model or os.getenv("CODEX_MEMORY_MODEL") or file_config.get("model", "gpt-5.5")
    api_mode = args.api_mode or file_config.get("api_mode", "responses")
    return APIConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        api_mode=api_mode,
        timeout_seconds=int(file_config.get("timeout_seconds", 180)),
        max_retries=int(file_config.get("max_retries", 1)),
    )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Reproduce a two-phase Codex-style memory workflow")
    p.add_argument("--workspace", type=Path, default=Path.cwd(), help="workspace containing memories/")
    p.add_argument("--config", help="optional JSON config; do not commit real keys")
    p.add_argument("--api-key", help="API key (prefer CODEX_MEMORY_API_KEY env var)")
    p.add_argument("--base-url", help="OpenAI-compatible API base URL, normally ending in /v1")
    p.add_argument("--model", help="model name supported by the selected endpoint")
    p.add_argument("--api-mode", choices=("responses", "chat_completions"))
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the memories/ layout")

    extract = sub.add_parser("extract", help="run Phase 1 for one rollout")
    extract.add_argument("rollout", type=Path)
    extract.add_argument("--rollout-id")
    extract.add_argument("--rollout-cwd", default="")
    extract.add_argument("--apply", action="store_true", help="materialize Phase 1 files")

    consolidate = sub.add_parser("consolidate", help="run Phase 2 over staged memories")
    consolidate.add_argument("--apply", action="store_true", help="atomically write MEMORY files")

    run = sub.add_parser("run", help="Phase 1 followed by Phase 2")
    run.add_argument("rollout", type=Path)
    run.add_argument("--rollout-id")
    run.add_argument("--rollout-cwd", default="")
    run.add_argument("--apply", action="store_true", help="materialize and consolidate")

    sub.add_parser("context", help="print the injectable memory summary context")
    sub.add_parser("show", help="print paths and hashes, not file bodies")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = MemoryStore(args.workspace)
    try:
        if args.command == "init":
            store.initialize()
            print(store.memories)
            return 0
        if args.command == "context":
            print(build_context(store))
            return 0
        if args.command == "show":
            store.initialize()
            import hashlib
            for name in ("MEMORY.md", "memory_summary.md", "raw_memories.md", "audit.jsonl"):
                path = store.memories / name
                if path.exists():
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    print(f"{name}\t{path.stat().st_size}\t{digest}")
            return 0

        client = ModelClient(_config(args))
        if args.command in {"extract", "run"}:
            phase1 = run_phase1(
                client, args.rollout,
                rollout_id=args.rollout_id,
                rollout_cwd=args.rollout_cwd,
            )
            print(result_as_json(phase1))
            if args.apply:
                materialized = materialize_phase1(store, phase1)
                print(f"phase1_materialized={materialized}", file=sys.stderr)
            elif args.command == "run":
                print("run without --apply stops after Phase 1 preview", file=sys.stderr)
                return 0
            if args.command == "extract":
                return 0

        phase2 = run_phase2(client, store)
        print(phase2.memory_diff)
        print(phase2.summary_diff)
        print(f"change_summary: {phase2.change_summary}")
        if args.apply:
            backup = apply_phase2(store, phase2)
            print(f"backup={backup}", file=sys.stderr)
        else:
            print("dry run: use --apply to write files", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

