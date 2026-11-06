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


def rec(mid, cache_read=0, cache_write=0, inp=0, out=0, rtype="assistant", model=None, content=None):
    message = {
        "id": mid,
        "role": "assistant",
        "usage": {
            "input_tokens": inp,
            "output_tokens": out,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        },
    }
    if model is not None:
        message["model"] = model
    if content is not None:
        message["content"] = content
    return {"type": rtype, "message": message}


SLASH = ("<command-message>agentic-os:wrap-up</command-message>\n"
         "<command-name>/agentic-os:wrap-up</command-name>")


def slash_rec(as_blocks=False, text=SLASH):
    """A slash command as the transcript stores it: a user message, content a string or a list of blocks."""
    content = [{"type": "text", "text": text}] if as_blocks else text
    return {"type": "user", "message": {"role": "user", "content": content}}


def skill_rec(mid, skill="agentic-os:wrap-up", name="Skill", **kw):
    block = {"type": "tool_use", "id": "toolu_" + mid, "name": name, "input": {"skill": skill}}
    return rec(mid, content=[block], **kw)


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
        write_jsonl(self.transcript, [slash_rec(), rec("m1", cache_read=1000, cache_write=50000, out=10)])
        p = run(self.transcript, "--append-trace", mem, "--task", "wrap-up", "--since-wrapup")
        self.assertEqual(p.returncode, 0, p.stderr)
        trace = os.path.join(mem, "metrics", "cost-trace.jsonl")
        self.assertTrue(os.path.isfile(trace))
        with open(trace, encoding="utf-8") as f:
            entry = json.loads(f.read().strip())
        self.assertIs(entry["estimate"], False)
        self.assertEqual(entry["task"], "wrap-up")
        self.assertEqual(entry["api_calls"], 1)

    def test_append_trace_nested_store_guard(self):
        """DCO-9192: a relative default mem-dir with a cwd already inside
        .agent-memory must not create .agent-memory/.agent-memory/metrics
        (mirrors cost-trace.sh's guard, b34d055)."""
        write_jsonl(self.transcript, [rec("m1", cache_read=100, out=5)])
        mem = os.path.join(self.tmp, ".agent-memory")
        nested_cwd = os.path.join(mem, "working")
        os.makedirs(nested_cwd, exist_ok=True)
        proc = subprocess.run(
            [sys.executable, SCRIPT, self.transcript, "--append-trace", ".agent-memory"],
            capture_output=True, text=True, encoding="utf-8", cwd=nested_cwd, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        d = json.loads(proc.stdout)
        self.assertTrue(d["ok"])
        self.assertIn("nested-store guard", d.get("trace_warning", ""))
        self.assertFalse(os.path.isdir(os.path.join(nested_cwd, ".agent-memory")))

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


class TestWrapupWindow(unittest.TestCase):
    """F18: a wrap-up row measures the wrap-up, a session row the whole session, and both say what they priced."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="measure-window-")
        self.transcript = os.path.join(self.tmp, "session.jsonl")
        self.mem = os.path.join(self.tmp, ".agent-memory")

    def measure(self, *args):
        p = run(self.transcript, *args)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def trace_rows(self):
        with open(os.path.join(self.mem, "metrics", "cost-trace.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def three_then_two(self, start):
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1), rec("a2", cache_read=10, out=1),
                                      rec("a3", cache_read=10, out=1), start,
                                      rec("w1", cache_read=100, out=2), rec("w2", cache_read=100, out=2)])

    def test_since_wrapup_counts_only_the_window(self):
        self.three_then_two(slash_rec())
        d = self.measure("--since-wrapup")
        self.assertEqual(d["api_calls"], 2)
        self.assertEqual(d["window"], "wrap-up")
        self.assertEqual(d["totals"]["cache_read"], 200)

    def test_without_the_flag_the_whole_session_is_measured(self):
        self.three_then_two(slash_rec())
        d = self.measure()
        self.assertEqual(d["api_calls"], 5)
        self.assertEqual(d["window"], "session")

    def test_slash_content_as_list_of_blocks(self):
        self.three_then_two(slash_rec(as_blocks=True))
        d = self.measure("--since-wrapup")
        self.assertEqual((d["api_calls"], d["window"]), (2, "wrap-up"))

    def test_quoted_marker_in_assistant_text_is_not_a_start(self):
        quote = "<command-name>/agentic-os:wrap-up</command-name>"
        write_jsonl(self.transcript, [
            rec("a1", cache_read=10, out=1, content=[{"type": "text", "text": "Ich zitiere: " + quote}]),
            {"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": quote}]}},
            {"type": "user", "message": {"role": "user", "content": "Notiz: " + quote}},
            rec("a2", cache_read=10, out=1)])
        d = self.measure("--since-wrapup")
        self.assertEqual((d["api_calls"], d["window"]), (2, "session"))

    def test_skill_tool_marks_the_start(self):
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1), skill_rec("s1", cache_read=20, out=1),
                                      rec("w1", cache_read=100, out=2)])
        d = self.measure("--since-wrapup")
        self.assertEqual((d["api_calls"], d["window"]), (2, "wrap-up"))

    def test_only_the_full_skill_name_counts(self):
        write_jsonl(self.transcript, [
            skill_rec("s1", skill="wrap-up", cache_read=10, out=1),
            skill_rec("s2", skill="agentic-os:wrap-up-extra", cache_read=10, out=1),
            skill_rec("s3", skill="agentic-os:wrap-up", name="Task", cache_read=10, out=1),
            rec("a1", cache_read=10, out=1)])
        d = self.measure("--since-wrapup")
        self.assertEqual((d["api_calls"], d["window"]), (4, "session"))

    def test_a_message_split_across_records_counts_when_its_tool_use_is_in_the_window(self):
        text = rec("m1", cache_read=20, out=1, content=[{"type": "text", "text": "ich starte wrap-up"}])
        tool = skill_rec("m1", cache_read=20, out=1)
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1), text, tool, rec("w1", cache_read=100, out=2)])
        d = self.measure("--since-wrapup")
        self.assertEqual((d["api_calls"], d["window"]), (2, "wrap-up"))

    def test_the_last_start_wins(self):
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1), slash_rec(), rec("w1", cache_read=10, out=1),
                                      rec("b1", cache_read=10, out=1), skill_rec("s1", cache_read=10, out=1),
                                      rec("w2", cache_read=10, out=1)])
        d = self.measure("--since-wrapup")
        self.assertEqual(d["api_calls"], 2)

    def test_wrapup_start_line_is_the_line_index(self):
        sys.path.insert(0, os.path.dirname(SCRIPT))
        import measure_session_cost as m
        self.three_then_two(slash_rec())
        self.assertEqual(m.wrapup_start_line(self.transcript), 3)
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1)])
        self.assertIsNone(m.wrapup_start_line(self.transcript))
        self.assertIsNone(m.wrapup_start_line(os.path.join(self.tmp, "nope.jsonl")))

    def test_no_marker_is_labelled_session(self):
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1), rec("a2", cache_read=10, out=1)])
        d = self.measure("--since-wrapup", "--append-trace", self.mem, "--task", "wrap-up")
        self.assertTrue(d["ok"])
        self.assertEqual(d["api_calls"], 2)
        row = self.trace_rows()[0]
        self.assertEqual((row["task"], row["window"]), ("session", "session"))

    def test_task_wrapup_without_the_flag_is_labelled_session(self):
        self.three_then_two(slash_rec())
        self.measure("--append-trace", self.mem, "--task", "wrap-up")
        self.assertEqual(self.trace_rows()[0]["task"], "session")

    def test_wrapup_window_keeps_the_wrapup_label(self):
        self.three_then_two(slash_rec())
        self.measure("--since-wrapup", "--append-trace", self.mem, "--task", "wrap-up")
        row = self.trace_rows()[0]
        self.assertEqual((row["task"], row["window"], row["api_calls"]), ("wrap-up", "wrap-up", 2))

    def test_models_and_price_basis_in_trace(self):
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1, model="claude-b"),
                                      rec("a2", cache_read=10, out=1, model="claude-a"),
                                      rec("a3", cache_read=10, out=1, model="claude-b"),
                                      rec("a4", cache_read=10, out=1)])
        d = self.measure("--append-trace", self.mem)
        self.assertEqual(d["models"], ["claude-a", "claude-b"])
        row = self.trace_rows()[0]
        self.assertEqual(row["models"], ["claude-a", "claude-b"])
        self.assertEqual(row["price_basis"], "opus-5-list")

    def test_models_cover_only_the_window(self):
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1, model="claude-old"), slash_rec(),
                                      rec("w1", cache_read=10, out=1, model="claude-new")])
        self.assertEqual(self.measure("--since-wrapup")["models"], ["claude-new"])

    def test_custom_rates_say_so(self):
        write_jsonl(self.transcript, [rec("a1", cache_read=10, out=1)])
        d = self.measure("--rates", "1,1,1,1", "--append-trace", self.mem)
        self.assertEqual(d["price_basis"], "custom")
        self.assertEqual(self.trace_rows()[0]["price_basis"], "custom")

    def test_since_wrapup_on_a_missing_file_stays_fail_soft(self):
        p = run(os.path.join(self.tmp, "nope.jsonl"), "--since-wrapup")
        self.assertEqual(p.returncode, 0)
        self.assertFalse(json.loads(p.stdout)["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
