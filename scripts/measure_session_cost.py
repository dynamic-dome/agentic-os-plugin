#!/usr/bin/env python3
"""measure_session_cost.py — MEASURED per-call cost facts from a Claude Code transcript.

Why this exists
---------------
`cost-trace.sh` writes ESTIMATES (`context_bytes / 4`, `"estimate": true`) because a
skill cannot see its own token counts. Estimates are not evidence: the first manual
transcript analysis of a wrap-up run was 2.77x too high
because it summed one record per CONTENT BLOCK while `usage` is reported once per API
RESPONSE. This script reads the transcript and reports what actually happened.

The two facts it is built to surface:

  1. Cost is the SUM of context length over API calls. The model is stateless, so every
     call resends the whole conversation; extra turns are the cost driver, not output.
  2. `cache_creation` (a prefix rewrite) is billed at 12.5x `cache_read`. A handful of
     rewrite events can dominate a whole session, so they are reported individually
     instead of being averaged away.

Contract: fail-soft. Always exit 0, always exactly one JSON object on stdout (except
`--help`, which prints help and no JSON so stdout stays parseable).

Usage:
  python scripts/measure_session_cost.py <transcript.jsonl> [--rewrite-threshold N]
                                         [--top N] [--human] [--append-trace <mem-dir>]

Transcripts live in ~/.claude/projects/<project-slug>/<session-id>.jsonl
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

# Opus-5 list prices, USD per token. Override with --rates "in,cache_read,cache_write,out".
DEFAULT_RATES = {
    "input": 15.0 / 1_000_000,
    "cache_read": 1.5 / 1_000_000,
    "cache_creation": 18.75 / 1_000_000,
    "output": 75.0 / 1_000_000,
}

DEFAULT_REWRITE_THRESHOLD = 20_000


def _harden_stdout():
    """err-008: Python's default stdout encoding on Windows is cp1252; a single
    non-ASCII character in a path would raise UnicodeEncodeError and break the
    fail-soft contract from the inside."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _emit(obj):
    try:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    except Exception:
        print('{"ok": false, "error": "serialization failed"}')
    return 0


def _fail(msg, **extra):
    payload = {"ok": False, "error": msg, "api_calls": 0}
    payload.update(extra)
    return _emit(payload)


def parse_transcript(path, rewrite_threshold):
    """Return (calls, malformed) where calls is an ordered list of per-API-call usage.

    Dedup key is `message.id`: the transcript writes one record per content block
    (text / thinking / tool_use) and repeats the SAME usage object in each. Summing
    over records instead of over ids is exactly the 2.77x error (err-010).
    """
    calls = []
    seen = set()
    malformed = 0

    # utf-8-sig: a BOM would otherwise glue itself to the first record's JSON
    # and silently drop that line as malformed (Codex review 2026-07-27).
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                malformed += 1
                continue
            if not isinstance(rec, dict):
                malformed += 1
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            mid = msg.get("id")
            if not isinstance(usage, dict) or not mid or mid in seen:
                continue
            seen.add(mid)
            calls.append({
                "call": len(calls) + 1,
                "message_id": mid,
                "input": int(usage.get("input_tokens") or 0),
                "cache_read": int(usage.get("cache_read_input_tokens") or 0),
                "cache_creation": int(usage.get("cache_creation_input_tokens") or 0),
                "output": int(usage.get("output_tokens") or 0),
            })

    return calls, malformed


def analyse(calls, rates, rewrite_threshold, top):
    totals = {"input": 0, "cache_read": 0, "cache_creation": 0, "output": 0}
    for c in calls:
        for k in totals:
            totals[k] += c[k]

    transport_tokens = totals["input"] + totals["cache_read"] + totals["cache_creation"]
    transport_cost = (totals["input"] * rates["input"]
                      + totals["cache_read"] * rates["cache_read"]
                      + totals["cache_creation"] * rates["cache_creation"])
    output_cost = totals["output"] * rates["output"]
    total_cost = transport_cost + output_cost

    ctx_lengths = [c["input"] + c["cache_read"] + c["cache_creation"] for c in calls]

    rewrites = []
    for c in calls:
        if c["cache_creation"] >= rewrite_threshold:
            rewrites.append({
                "call": c["call"],
                "cache_creation": c["cache_creation"],
                "cache_read": c["cache_read"],
                "est_usd": round(c["cache_creation"] * rates["cache_creation"], 4),
            })

    rewrite_cost = sum(r["est_usd"] for r in rewrites)

    result = {
        "ok": True,
        "estimate": False,
        "api_calls": len(calls),
        "totals": totals,
        "cost_usd": {
            "transport": round(transport_cost, 4),
            "output": round(output_cost, 4),
            "total": round(total_cost, 4),
        },
        "transport_share": round(transport_tokens / (transport_tokens + totals["output"]), 4)
                           if (transport_tokens + totals["output"]) else 0.0,
        "context_len": {
            "first": ctx_lengths[0] if ctx_lengths else 0,
            "last": ctx_lengths[-1] if ctx_lengths else 0,
            "mean": int(sum(ctx_lengths) / len(ctx_lengths)) if ctx_lengths else 0,
            "sum": sum(ctx_lengths),
        },
        "rewrite_events": rewrites,
        "rewrite_cost_usd": round(rewrite_cost, 4),
        "rewrite_share_of_total": round(rewrite_cost / total_cost, 4) if total_cost else 0.0,
    }
    if top:
        result["most_expensive_calls"] = sorted(
            ({"call": c["call"],
              "context": c["input"] + c["cache_read"] + c["cache_creation"],
              "output": c["output"]} for c in calls),
            key=lambda x: x["context"], reverse=True)[:top]
    return result


def append_trace(mem_dir, result, task):
    """Write a MEASURED record next to cost-trace.sh's estimates ("estimate": false).
    Fail-soft: a failed trace never changes the exit code or the stdout payload."""
    try:
        metrics = os.path.join(mem_dir, "metrics")
        os.makedirs(metrics, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "task": task,
            "estimate": False,
            "api_calls": result["api_calls"],
            "context_tokens_sum": result["context_len"]["sum"],
            "output_tokens": result["totals"]["output"],
            "cost_usd": result["cost_usd"]["total"],
            "rewrite_events": len(result["rewrite_events"]),
            "rewrite_cost_usd": result["rewrite_cost_usd"],
        }
        with open(os.path.join(metrics, "cost-trace.jsonl"), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return None
    except Exception as exc:
        return str(exc)


def human_report(r):
    lines = [
        f"API-Calls (dedup message.id): {r['api_calls']}",
        f"Kontext-Transport: {r['transport_share'] * 100:.1f}% des Token-Volumens",
        f"Kosten: ${r['cost_usd']['total']:.2f}  "
        f"(Transport ${r['cost_usd']['transport']:.2f} / Output ${r['cost_usd']['output']:.2f})",
        f"Kontextlaenge: erst {r['context_len']['first']:,} -> zuletzt {r['context_len']['last']:,}"
        f" (Mittel {r['context_len']['mean']:,}, Summe {r['context_len']['sum']:,})",
        f"Prefix-Rewrites: {len(r['rewrite_events'])} Events, ${r['rewrite_cost_usd']:.2f}"
        f" = {r['rewrite_share_of_total'] * 100:.0f}% der Gesamtkosten",
    ]
    for e in r["rewrite_events"]:
        lines.append(f"  #{e['call']:>3}  write={e['cache_creation']:>9,}"
                     f"  read={e['cache_read']:>9,}  ${e['est_usd']:.2f}")
    return "\n".join(lines)


def main(argv):
    _harden_stdout()

    parser = argparse.ArgumentParser(
        prog="measure_session_cost.py",
        description="Measured per-API-call cost facts from a Claude Code transcript.")
    parser.add_argument("transcript", nargs="?", help="path to <session-id>.jsonl")
    parser.add_argument("--rewrite-threshold", type=int, default=DEFAULT_REWRITE_THRESHOLD,
                        help="cache_creation tokens that count as a prefix rewrite")
    parser.add_argument("--top", type=int, default=0, help="also list the N most expensive calls")
    parser.add_argument("--human", action="store_true", help="human-readable report on stdout")
    parser.add_argument("--locate", metavar="SESSION_ID", default=None,
                        help="find the transcript as <projects-root>/*/<SESSION_ID>.jsonl "
                             "instead of passing a path (wrap-up knows only its id, T-016)")
    parser.add_argument("--projects-root", default=None,
                        help="transcript root for --locate (default: ~/.claude/projects)")
    parser.add_argument("--append-trace", metavar="MEM_DIR", default=None,
                        help="append a measured record to <MEM_DIR>/metrics/cost-trace.jsonl")
    parser.add_argument("--task", default="session", help="task label for --append-trace")
    parser.add_argument("--rates", default=None,
                        help="override prices: input,cache_read,cache_creation,output (USD per 1M)")

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # L25: argparse exits 0 after printing --help (stdout must stay JSON-free) and
        # non-zero on a parse error (fail-soft JSON instead of a hard exit).
        if exc.code == 0:
            return 0
        return _fail("argument parsing failed")

    if args.transcript and args.locate:
        return _fail("give either a transcript path or --locate, not both")
    if args.locate:
        # Session ids are UUID-shaped. Anything else (glob syntax, path
        # separators, traversal) must fail instead of resolving an arbitrary
        # transcript (Codex review 2026-07-27).
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", args.locate):
            return _fail("invalid session id for --locate", session_id=args.locate)
        # One-level glob only - a recursive walk over ~/.claude/projects is
        # exactly the scandir-vs-rglob trap on Windows. The sid is unique in
        # practice; on a collision the newest mtime is the running session.
        root = args.projects_root or os.path.join(
            os.path.expanduser("~"), ".claude", "projects")
        matches = glob.glob(os.path.join(root, "*", args.locate + ".jsonl"))
        if not matches:
            return _fail("no transcript for session id", session_id=args.locate,
                         projects_root=root)
        args.transcript = max(matches, key=os.path.getmtime)
    if not args.transcript:
        return _fail("no transcript given")

    rates = dict(DEFAULT_RATES)
    if args.rates:
        try:
            vals = [float(x) for x in args.rates.split(",")]
            if len(vals) != 4:
                raise ValueError
            for key, val in zip(("input", "cache_read", "cache_creation", "output"), vals):
                rates[key] = val / 1_000_000
        except Exception:
            return _fail("--rates needs 4 comma-separated numbers (USD per 1M tokens)")

    if not os.path.isfile(args.transcript):
        return _fail("transcript not found", transcript=args.transcript)

    try:
        calls, malformed = parse_transcript(args.transcript, args.rewrite_threshold)
    except Exception as exc:
        return _fail("transcript unreadable: %s" % exc, transcript=args.transcript)

    result = analyse(calls, rates, args.rewrite_threshold, args.top)
    result["transcript"] = args.transcript
    result["malformed_lines"] = malformed

    if args.append_trace:
        err = append_trace(args.append_trace, result, args.task)
        if err:
            result["trace_warning"] = err

    if args.human:
        try:
            print(human_report(result))
        except Exception:
            return _emit(result)
        return 0

    return _emit(result)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]) or 0)
