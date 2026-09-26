#!/usr/bin/env python3
"""Tests for scripts/measure_session_cost.py.

The script turns a Claude Code transcript into MEASURED per-call cost facts.
Its whole reason to exist is that estimates lied: cost-trace.sh writes
context_bytes/4 guesses, and the first manual transcript analysis was 2.77x too
high because it summed per-content-block records instead of per-API-call.

Contract under test:
  1. dedup by message.id            <- the 2.77x regression (err-010)
  2. fail-soft: always exit 0, always one JSON object on stdout
  3. --help prints help, exits 0, writes NO JSON (stdout purity, L25)
  4. trailing flag without value must not hang (L26)
  5. utf-8 reads on Windows (L1)
  6. cache_creation events are reported separately - they are the expensive ones
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "scripts", "measure_session_cost.py")


def rec(mid, cache_read=0, cache_write=0, inp=0, out=0, rtype="assistant"):
    return {
        "type": rtype,
        "message": {
            "id": mid,
            "role": "assistant",
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_write,
            },
        },
    }


def write_jsonl(path, records):
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def run(*args, timeout=30):
    proc = subprocess.run(
        [sys.executable, SCRIPT] + list(args),
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
    )
    return proc


class TestMeasureSessionCost(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="measure-cost-")
        self.transcript = os.path.join(self.tmp, "session.jsonl")

    # --- 1. the 2.77x regression -------------------------------------------
    def test_deduplicates_by_message_id(self):
        """Three records, two sharing a message.id, are TWO API calls."""
        write_jsonl(self.transcript, [
            rec("msg_a", cache_write=1000, out=10),
            rec("msg_a", cache_write=1000, out=10),   # same API response, 2nd block
            rec("msg_b", cache_read=1000, out=20),
        ])
        p = run(self.transcript)
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertEqual(d["api_calls"], 2)
        self.assertEqual(d["totals"]["cache_creation"], 1000)
        self.assertEqual(d["totals"]["output"], 30)

    def test_naive_sum_would_be_higher(self):
        """Guard against a future 'optimization' that drops the dedup."""
        write_jsonl(self.transcript, [rec("msg_x", cache_read=500, out=5)] * 4)
        d = json.loads(run(self.transcript).stdout)
        self.assertEqual(d["api_calls"], 1)
        self.assertEqual(d["totals"]["cache_read"], 500)

    # --- 2. fail-soft -------------------------------------------------------
    def test_missing_file_is_fail_soft(self):
        p = run(os.path.join(self.tmp, "nope.jsonl"))
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertFalse(d["ok"])
        self.assertIn("error", d)

    def test_empty_file(self):
        open(self.transcript, "w", encoding="utf-8").close()
        p = run(self.transcript)
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertEqual(d["api_calls"], 0)

    def test_malformed_lines_are_skipped(self):
        with open(self.transcript, "w", encoding="utf-8") as f:
            f.write("not json at all\n")
            f.write(json.dumps(rec("msg_ok", cache_read=100, out=1)) + "\n")
            f.write("{ broken\n")
            f.write("\n")
        p = run(self.transcript)
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertEqual(d["api_calls"], 1)
        self.assertEqual(d["malformed_lines"], 2)

    def test_records_without_usage_are_ignored(self):
        write_jsonl(self.transcript, [
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant", "message": {"id": "no_usage", "role": "assistant"}},
            rec("msg_real", cache_read=7, out=1),
        ])
        d = json.loads(run(self.transcript).stdout)
        self.assertEqual(d["api_calls"], 1)

    # --- 3. stdout purity ---------------------------------------------------
    def test_help_exits_zero_without_json(self):
        p = run("--help")
        self.assertEqual(p.returncode, 0)
        self.assertIn("usage", p.stdout.lower())
        with self.assertRaises(ValueError):
            json.loads(p.stdout)

    def test_no_args_is_fail_soft_json(self):
        p = run()
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertFalse(d["ok"])

    # --- 4. trailing flag must not hang ------------------------------------
    def test_trailing_flag_without_value_does_not_hang(self):
        p = run(self.transcript, "--top", timeout=15)
        self.assertEqual(p.returncode, 0)
        json.loads(p.stdout)

    # --- 5. windows/utf-8 ---------------------------------------------------
    def test_non_ascii_transcript(self):
        r = rec("msg_umlaut", cache_read=50, out=2)
        r["message"]["content"] = [{"type": "text", "text": "Grüße — Präfix ändern"}]
        write_jsonl(self.transcript, [r])
        p = run(self.transcript)
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertEqual(d["api_calls"], 1)

    # --- 6. the expensive events -------------------------------------------
    def test_reports_cache_write_events(self):
        """Prefix rewrites are 12.5x a read - they must be visible, not buried."""
        write_jsonl(self.transcript, [
            rec("m1", cache_write=60000, out=10),
            rec("m2", cache_read=60000, cache_write=500, out=10),
            rec("m3", cache_read=20000, cache_write=90000, out=10),
        ])
        d = json.loads(run(self.transcript, "--rewrite-threshold", "20000").stdout)
        self.assertEqual(len(d["rewrite_events"]), 2)
        self.assertEqual(d["rewrite_events"][0]["call"], 1)
        self.assertEqual(d["rewrite_events"][1]["cache_creation"], 90000)

    # --- 7. measured trace records -----------------------------------------
    def test_append_trace_writes_measured_record(self):
        """The point of the script: replace cost-trace.sh's estimate:true guesses."""
        mem = os.path.join(self.tmp, ".agent-memory")
        write_jsonl(self.transcript, [rec("m1", cache_read=1000, cache_write=50000, out=10)])
        p = run(self.transcript, "--append-trace", mem, "--task", "wrap-up")
        self.assertEqual(p.returncode, 0, p.stderr)
        trace = os.path.join(mem, "metrics", "cost-trace.jsonl")
        self.assertTrue(os.path.isfile(trace))
        with open(trace, encoding="utf-8") as f:
            entry = json.loads(f.read().strip())
        self.assertIs(entry["estimate"], False)
        self.assertEqual(entry["task"], "wrap-up")
        self.assertEqual(entry["api_calls"], 1)

    def test_append_trace_failure_stays_fail_soft(self):
        """A trace that cannot be written must not change exit code or stdout."""
        write_jsonl(self.transcript, [rec("m1", cache_read=10, out=1)])
        # a FILE where the mem dir is expected -> makedirs must fail
        blocker = os.path.join(self.tmp, "blocked")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("not a directory")
        p = run(self.transcript, "--append-trace", blocker)
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertTrue(d["ok"])
        self.assertIn("trace_warning", d)

    # --- 8. --locate: find own transcript from a session id -----------------
    def _projects(self, *sids_mtimes):
        """Build <tmp>/projects/<proj-i>/<sid>.jsonl fixtures; returns root."""
        root = os.path.join(self.tmp, "projects")
        for i, (sid, mtime) in enumerate(sids_mtimes):
            d = os.path.join(root, f"proj-{i}")
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, f"{sid}.jsonl")
            write_jsonl(path, [rec(f"m-{sid}", cache_read=100, out=5)])
            os.utime(path, (mtime, mtime))
        return root

    def test_locate_finds_transcript_by_session_id(self):
        """wrap-up knows only its session id, never its transcript path (T-016)."""
        root = self._projects(("sid-abc", 1000))
        p = run("--locate", "sid-abc", "--projects-root", root)
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertTrue(d["ok"])
        self.assertEqual(d["api_calls"], 1)
        self.assertIn("sid-abc.jsonl", d["transcript"])

    def test_locate_newest_mtime_wins_on_duplicate_sid(self):
        root = self._projects(("sid-dup", 1000), ("sid-dup", 2000))
        d = json.loads(run("--locate", "sid-dup", "--projects-root", root).stdout)
        self.assertIn(os.path.join("proj-1", "sid-dup.jsonl"), d["transcript"])

    def test_locate_missing_sid_is_fail_soft(self):
        root = self._projects(("sid-abc", 1000))
        p = run("--locate", "sid-nope", "--projects-root", root)
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertFalse(d["ok"])

    def test_locate_and_positional_transcript_conflict(self):
        """Two sources of truth for the same input -> reject, don't guess."""
        root = self._projects(("sid-abc", 1000))
        p = run(self.transcript, "--locate", "sid-abc", "--projects-root", root)
        self.assertEqual(p.returncode, 0)
        d = json.loads(p.stdout)
        self.assertFalse(d["ok"])

    def test_locate_rejects_glob_syntax(self):
        """Codex review 2026-07-27: --locate '*' resolved an arbitrary
        transcript successfully instead of failing."""
        root = self._projects(("sid-abc", 1000))
        for sneaky in ("*", "sid-*", "..", "../sid-abc", "a/b"):
            p = run("--locate", sneaky, "--projects-root", root)
            self.assertEqual(p.returncode, 0)
            d = json.loads(p.stdout)
            self.assertFalse(d["ok"], f"glob/traversal id {sneaky!r} must not resolve")

    def test_bom_transcript_still_parses(self):
        """utf-8-sig: a BOM before the first record must not eat the whole file."""
        with open(self.transcript, "w", encoding="utf-8-sig") as f:
            f.write(json.dumps(rec("m1", cache_read=100, out=5)) + "\n")
        d = json.loads(run(self.transcript).stdout)
        self.assertEqual(d["api_calls"], 1)
        self.assertEqual(d["malformed_lines"], 0)

    def test_cost_breakdown_present(self):
        write_jsonl(self.transcript, [rec("m1", cache_read=1000000, out=1000)])
        d = json.loads(run(self.transcript).stdout)
        self.assertIn("cost_usd", d)
        self.assertIn("transport", d["cost_usd"])
        self.assertIn("output", d["cost_usd"])
        self.assertGreater(d["cost_usd"]["total"], 0)
        self.assertIn("transport_share", d)


if __name__ == "__main__":
    unittest.main(verbosity=2)
