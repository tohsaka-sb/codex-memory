"""Atomic, auditable storage for the file-based memory workspace."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SAFE = re.compile(r"[^a-z0-9._-]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str, fallback: str = "memory") -> str:
    cleaned = SAFE.sub("-", value.strip().lower()).strip("-._")
    return (cleaned or fallback)[:80]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MaterializedExtraction:
    raw_memory_file: Path
    rollout_summary_file: Path
    raw_memories_file: Path


class MemoryStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace.resolve()
        self.memories = self.workspace / "memories"
        self.rollout_summaries = self.memories / "rollout_summaries"
        self.raw_memory_dir = self.memories / "raw_memories"
        self.history = self.memories / ".history"
        self.audit = self.memories / "audit.jsonl"
        self.lock_path = self.memories / ".memory.lock"

    def initialize(self) -> None:
        self.rollout_summaries.mkdir(parents=True, exist_ok=True)
        self.raw_memory_dir.mkdir(parents=True, exist_ok=True)
        self.history.mkdir(parents=True, exist_ok=True)
        if not (self.memories / "MEMORY.md").exists():
            self.atomic_write(self.memories / "MEMORY.md", "")
        if not (self.memories / "memory_summary.md").exists():
            self.atomic_write(
                self.memories / "memory_summary.md",
                "v1\n\n## User Profile\n\nNo durable profile recorded yet.\n\n"
                "## User preferences\n\n## General Tips\n\n## What's in Memory\n",
            )
        if not (self.memories / "raw_memories.md").exists():
            self.atomic_write(
                self.memories / "raw_memories.md",
                "# Raw Memories\n\nMerged Phase-1 raw memories keyed by rollout id.\n",
            )

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.memories.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read(self, relative: str, default: str = "") -> str:
        path = self.memories / relative
        if not path.exists():
            return default
        return path.read_text(encoding="utf-8")

    def atomic_write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if text and not text.endswith("\n"):
            text += "\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600 if path.name.endswith(".jsonl") else 0o644)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def backup(self, *paths: Path) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        target = self.history / stamp
        target.mkdir(parents=True, exist_ok=False)
        for path in paths:
            if path.exists():
                self.atomic_write(target / path.name, path.read_text(encoding="utf-8"))
        return target

    def append_audit(self, record: dict) -> None:
        safe = dict(record)
        safe.setdefault("timestamp", utc_now())
        with self.audit.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")

    def materialize_extraction(
        self,
        *,
        rollout_id: str,
        rollout_path: str,
        rollout_cwd: str,
        rollout_slug: str,
        rollout_summary: str,
        raw_memory: str,
    ) -> MaterializedExtraction:
        self.initialize()
        rid = safe_name(rollout_id, "unknown-rollout")
        slug = safe_name(rollout_slug, "memory")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        short = hashlib.sha256(rid.encode()).hexdigest()[:6]
        summary_name = f"{timestamp}-{short}-{slug}.md"
        summary_path = self.rollout_summaries / summary_name
        raw_path = self.raw_memory_dir / f"{rid}.md"
        metadata = (
            f"thread_id: {rid}\n"
            f"updated_at: {utc_now()}\n"
            f"rollout_path: {rollout_path}\n"
            f"cwd: {rollout_cwd}\n"
        )
        self.atomic_write(summary_path, metadata + "\n" + rollout_summary)
        self.atomic_write(raw_path, metadata + f"rollout_summary_file: {summary_name}\n\n" + raw_memory)

        merged_path = self.memories / "raw_memories.md"
        merged = merged_path.read_text(encoding="utf-8")
        begin = f"<!-- BEGIN RAW MEMORY {rid} -->"
        end = f"<!-- END RAW MEMORY {rid} -->"
        block = f"{begin}\n\n{metadata}rollout_summary_file: {summary_name}\n\n{raw_memory.rstrip()}\n\n{end}"
        pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
        if pattern.search(merged):
            merged = pattern.sub(block, merged)
        else:
            merged = merged.rstrip() + "\n\n" + block + "\n"
        self.atomic_write(merged_path, merged)
        self.append_audit(
            {
                "event": "phase1_materialized",
                "rollout_id": rid,
                "slug": slug,
                "summary_file": summary_name,
                "raw_memory_sha256": sha256_text(raw_memory),
                "rollout_summary_sha256": sha256_text(rollout_summary),
            }
        )
        return MaterializedExtraction(raw_path, summary_path, merged_path)

