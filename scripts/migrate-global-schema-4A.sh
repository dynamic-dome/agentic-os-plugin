#!/usr/bin/env bash
# Agentic OS — Global Store Migration to 4.A provenance schema
# -------------------------------------------------------------
# Backfills the global cross-project layer (~/.claude-memory/global/) to the 4.A
# provenance contract: every pattern AND learning gains
#   id=G-<fact_type>-<n>, scope, valid_from, source_evidence, lifecycle, source_projects.
# Legacy learnings using the singular `source_project` are upgraded to `source_projects`.
#
# SAFETY:
#   - --dry-run is the DEFAULT. Nothing is written without --apply.
#   - --apply first backs up every *.json to *.4A.bak (skips backup if one exists).
#   - Idempotent: an already-migrated entry (id starts with G-, has scope+lifecycle) is
#     left untouched, so re-running is a no-op.
#   - NEVER deletes an entry. Row count in == row count out (asserted by the caller).
#
# Usage:
#   bash scripts/migrate-global-schema-4A.sh            # dry-run report
#   bash scripts/migrate-global-schema-4A.sh --apply    # backup + write

set +e
MODE="dry-run"
[ "$1" = "--apply" ] && MODE="apply"

GLOBAL_DIR="${CLAUDE_MEMORY_GLOBAL:-$HOME/.claude-memory/global}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$GLOBAL_DIR" ]; then
  echo "STOP: global store not found at $GLOBAL_DIR — nothing to migrate."
  exit 0
fi

python - "$GLOBAL_DIR" "$MODE" <<'PYEOF'
import sys, os, json, re, datetime

global_dir, mode = sys.argv[1], sys.argv[2]

def normalize(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()

# Legacy stores carry qualitative confidence ("low"/"medium"/"high") alongside numeric
# values. Coerce to a float so the promotion gate never sees float("low") and crashes.
_QUAL_CONF = {"very low": 0.1, "low": 0.3, "medium": 0.5, "high": 0.8, "very high": 0.9}
def coerce_conf(v, default=0.5):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _QUAL_CONF:
            return _QUAL_CONF[s]
        try:
            return float(s)
        except ValueError:
            return default
    return default

def compute_scope(ftype, tags):
    norm = sorted({(t or "").strip().lower() for t in (tags or []) if t and t.strip()})
    return f"{ftype}|{','.join(norm)}"

def mtime_iso(path):
    ts = os.path.getmtime(path)
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")

def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def already_migrated(e):
    return str(e.get("id", "")).startswith("G-") and "scope" in e and "lifecycle" in e

def migrate_entry(e, ftype, counter, default_from):
    """Return (migrated_entry, changed_bool). Idempotent."""
    if already_migrated(e):
        return e, False
    out = dict(e)
    # value: normalized cleartext from description/text/value
    desc = e.get("description") or e.get("text") or e.get("value") or ""
    out["value"] = normalize(desc)
    out["fact_type"] = ftype
    # source_projects: upgrade singular source_project -> list
    sp = e.get("source_projects")
    if not sp:
        single = e.get("source_project")
        sp = [single] if single else []
    out["source_projects"] = sp
    if "source_project" not in out and sp:
        out["source_project"] = sp[0]
    out["source_evidence"] = e.get("source_evidence") or e.get("evidence") or []
    out["confidence"] = coerce_conf(e.get("confidence", 0.5))
    out.setdefault("occurrences", e.get("occurrences", 1))
    vf = e.get("first_seen") or e.get("date") or default_from
    out["first_seen"] = vf
    out["valid_from"] = vf
    out.setdefault("valid_until", None)
    out.setdefault("last_relevant", e.get("last_relevant") or e.get("last_seen") or vf)
    # Lifecycle is decided by the SAME promotion gate Phase 2 enforces, so a backfilled
    # entry is never `active` unless it would actually pass the gate (no two classes of
    # active). An already-superseded/archived lifecycle is preserved as-is.
    prior = e.get("lifecycle")
    if prior in ("superseded", "archived"):
        out["lifecycle"] = prior
    else:
        conf = float(out["confidence"])
        occ = int(out["occurrences"])
        nproj = len(out["source_projects"])
        gate = conf >= 0.6 and occ >= 3 and nproj >= 2
        out["lifecycle"] = "active" if gate else "candidate"
    out["scope"] = compute_scope(ftype, e.get("tags") or [])
    out.setdefault("superseded_by", e.get("superseded_by"))
    out["id"] = f"G-{ftype}-{counter:03d}"
    out["previous_id"] = e.get("id")
    return out, True

def process(fname, ftype):
    path = os.path.join(global_dir, fname)
    if not os.path.exists(path):
        return None
    items = load(path)
    default_from = mtime_iso(path)
    migrated, changed = [], 0
    for i, e in enumerate(items, start=1):
        m, ch = migrate_entry(e, ftype, i, default_from)
        migrated.append(m)
        changed += 1 if ch else 0
    return path, items, migrated, changed

report = []
total_in = total_out = total_changed = 0
to_write = []
for fname, ftype in [("patterns.json", "pattern"), ("learnings.json", "learning")]:
    res = process(fname, ftype)
    if res is None:
        report.append(f"  {fname}: absent — skipped")
        continue
    path, src, dst, changed = res
    total_in += len(src); total_out += len(dst); total_changed += changed
    report.append(f"  {fname}: {len(src)} in -> {len(dst)} out, {changed} migrated, "
                  f"{len(src)-changed} already-4A")
    # show id mapping sample
    for e_old, e_new in list(zip(src, dst))[:3]:
        if e_new.get("previous_id"):
            report.append(f"      {e_old.get('id')} -> {e_new['id']} "
                          f"scope={e_new['scope']} |proj|={len(e_new['source_projects'])}")
    to_write.append((path, dst))

print(f"=== Global Schema 4.A Migration ({mode}) ===")
print(f"Store: {global_dir}")
print("\n".join(report))
print(f"TOTAL: {total_in} in -> {total_out} out, {total_changed} migrated "
      f"(invariant: in==out -> {'OK' if total_in==total_out else 'VIOLATED'})")

if total_in != total_out:
    print("ABORT: row-count invariant violated — refusing to write.")
    sys.exit(1)

if mode == "apply":
    for path, dst in to_write:
        bak = path + ".4A.bak"
        if not os.path.exists(bak):
            with open(bak, "w", encoding="utf-8") as f:
                json.dump(load(path), f, ensure_ascii=False, indent=2)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dst, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    # bump projects.json schema_version
    pj = os.path.join(global_dir, "projects.json")
    if os.path.exists(pj):
        data = load(pj)
        data["schema_version"] = "4A"
        bak = pj + ".4A.bak"
        if not os.path.exists(bak):
            with open(bak, "w", encoding="utf-8") as f:
                json.dump(load(pj), f, ensure_ascii=False, indent=2)
        tmp = pj + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, pj)
    print("APPLIED. Backups at *.4A.bak. projects.json schema_version=4A.")
else:
    print("DRY-RUN — no files written. Re-run with --apply to migrate.")
PYEOF