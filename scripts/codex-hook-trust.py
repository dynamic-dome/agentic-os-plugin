#!/usr/bin/env python3
"""Trust + enable agentic-os plugin hooks in Codex via the app-server API (T-24).

Codex runs unmanaged hooks only when their `trusted_hash` in config.toml
matches the discovered hook definition. Plugin hook commands contain the
VERSIONED plugin-cache path, so every plugin update changes the hash and
silently breaks trust (S0-e, membrain/memcodexlifecycle.md). Run this script
after every `agentic-os` plugin update as part of the deploy routine:

    python scripts/codex-hook-trust.py            # trust + enable
    python scripts/codex-hook-trust.py --dry-run  # show what would change

This is a deploy tool, not a hook: it fails LOUDLY on errors. It writes via
`config/batchWrite` (the conflict-free path — never edit config.toml by hand
while Codex sessions are running).
"""
import argparse
import json
import shutil
import subprocess
import sys
import threading
import time

DEFAULT_PLUGIN_ID = "agentic-os@agentic-os-marketplace"
DEFAULT_EVENTS = "sessionStart,postToolUse"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--plugin-id", default=DEFAULT_PLUGIN_ID)
    ap.add_argument("--events", default=DEFAULT_EVENTS,
                    help="comma-separated eventName whitelist (camelCase, e.g. sessionStart,postToolUse)")
    ap.add_argument("--cwd", default=None, help="project dir for hooks/list (default: current dir)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    events = {e.strip() for e in args.events.split(",") if e.strip()}
    import os
    cwd = args.cwd or os.getcwd()

    codex = shutil.which("codex")
    if not codex:
        print("FEHLER: codex nicht im PATH", file=sys.stderr)
        return 1

    proc = subprocess.Popen(
        [codex, "app-server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
    )
    results = {}

    def reader():
        for line in proc.stdout:
            try:
                d = json.loads(line.strip())
            except Exception:
                continue
            if "id" in d:
                results[d["id"]] = d

    threading.Thread(target=reader, daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def wait(rid, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if rid in results:
                return results[rid]
            time.sleep(0.2)
        return None

    try:
        send({"method": "initialize", "id": 1,
              "params": {"clientInfo": {"name": "codex-hook-trust", "title": "agentic-os deploy", "version": "1.0.0"}}})
        if not wait(1):
            print("FEHLER: initialize timeout", file=sys.stderr)
            return 1

        send({"method": "hooks/list", "id": 2, "params": {"cwds": [cwd]}})
        resp = wait(2)
        if not resp or "result" not in resp:
            print(f"FEHLER: hooks/list fehlgeschlagen: {resp}", file=sys.stderr)
            return 1

        hooks = resp["result"]["data"][0]["hooks"]
        state_value = {}
        for h in hooks:
            if h.get("pluginId") != args.plugin_id or h.get("eventName") not in events:
                continue
            status = "OK" if h.get("trustStatus") == "trusted" and h.get("enabled") else "WIRD GETRUSTET"
            print(f"{h['eventName']:<14} {h.get('trustStatus'):<10} enabled={h.get('enabled')} -> {status}")
            if status != "OK":
                state_value[h["key"]] = {"enabled": True, "trusted_hash": h["currentHash"]}

        if not state_value:
            print("Nichts zu tun — alle Ziel-Hooks bereits trusted + enabled.")
            return 0
        if args.dry_run:
            print(f"[dry-run] wuerde {len(state_value)} hooks.state-Eintraege schreiben.")
            return 0

        send({"method": "config/batchWrite", "id": 3, "params": {
            "edits": [{"keyPath": "hooks.state", "value": state_value, "mergeStrategy": "upsert"}],
            "reloadUserConfig": True,
        }})
        resp = wait(3)
        if not resp or resp.get("result", {}).get("status") != "ok":
            print(f"FEHLER: config/batchWrite fehlgeschlagen: {resp}", file=sys.stderr)
            return 1

        send({"method": "hooks/list", "id": 4, "params": {"cwds": [cwd]}})
        resp = wait(4)
        bad = []
        for h in resp["result"]["data"][0]["hooks"]:
            if h.get("pluginId") == args.plugin_id and h.get("eventName") in events:
                ok = h.get("trustStatus") == "trusted" and h.get("enabled")
                print(f"VERIFY {h['eventName']:<14} trust={h.get('trustStatus')} enabled={h.get('enabled')}")
                if not ok:
                    bad.append(h["key"])
        if bad:
            print(f"FEHLER: nicht getrustet nach Write: {bad}", file=sys.stderr)
            return 1
        print("Fertig — Ziel-Hooks trusted + enabled.")
        return 0
    finally:
        proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
