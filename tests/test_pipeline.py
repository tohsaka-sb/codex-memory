from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_memory.pipeline import apply_phase2, materialize_phase1, run_phase1, run_phase2
from codex_memory.redaction import redact_text
from codex_memory.storage import MemoryStore


class FakeClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def create_structured(self, **kwargs):
        self.prompts.append(kwargs)
        return self.outputs.pop(0)


class MemoryPipelineTests(unittest.TestCase):
    def test_redaction(self):
        text = 'Authorization: Bearer secret-token {"api_key":"sk-abcdefghijklmnop"}'
        redacted = redact_text(text)
        self.assertNotIn("secret-token", redacted)
        self.assertNotIn("sk-abcdefghijklmnop", redacted)

    def test_two_phase_apply(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rollout = root / "rollout-11111111-2222-3333-4444-555555555555.jsonl"
            rollout.write_text('{"role":"user","text":"never resume partial results"}\n')
            store = MemoryStore(root)
            fake = FakeClient([
                {
                    "rollout_summary": "# Summary\n\nThe user rejected partial resume.",
                    "rollout_slug": "no-partial-resume",
                    "raw_memory": "task_outcome: success\n\nPreference signals:\n- Never resume partial results.",
                },
                {
                    "memory_md": "# Task Group: experiment\n\n- Never resume partial results.\n",
                    "memory_summary_md": "v1\n\n## User preferences\n\n- Never resume partial results.\n",
                    "change_summary": "Added the durable no-resume preference.",
                },
            ])
            p1 = run_phase1(fake, rollout, rollout_cwd=str(root))
            materialize_phase1(store, p1)
            self.assertIn("Never resume", store.read("raw_memories.md"))
            self.assertEqual(len(list(store.rollout_summaries.glob("*.md"))), 1)

            p2 = run_phase2(fake, store)
            self.assertIn("Never resume", p2.memory_diff)
            backup = apply_phase2(store, p2)
            self.assertTrue(backup.is_dir())
            self.assertEqual(store.read("MEMORY.md"), p2.memory_md)
            self.assertEqual(store.read("memory_summary.md"), p2.memory_summary_md)
            self.assertEqual(len(fake.prompts), 2)

    def test_phase1_input_is_redacted_before_client(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "rollout.jsonl"
            path.write_text('Bearer should-not-leak sk-abcdefghijklmnop')
            fake = FakeClient([{"rollout_summary": "", "rollout_slug": "", "raw_memory": ""}])
            run_phase1(fake, path)
            prompt = fake.prompts[0]["user_prompt"]
            self.assertNotIn("should-not-leak", prompt)
            self.assertNotIn("sk-abcdefghijklmnop", prompt)

    def test_phase2_input_is_redacted_before_client(self):
        with tempfile.TemporaryDirectory() as td:
            store = MemoryStore(Path(td))
            store.initialize()
            store.atomic_write(store.memories / "MEMORY.md", "Bearer private-token")
            fake = FakeClient([{
                "memory_md": "# Clean\n",
                "memory_summary_md": "v1\n\n## Clean\n",
                "change_summary": "Removed a secret-like value.",
            }])
            run_phase2(fake, store)
            prompt = fake.prompts[0]["user_prompt"]
            self.assertNotIn("private-token", prompt)


if __name__ == "__main__":
    unittest.main()

