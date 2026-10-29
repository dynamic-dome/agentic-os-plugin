#!/usr/bin/env python3
"""migrate_identity.py — move every project profile into the central profile (P2, Teil B; a data run, not a release).

WHY: dozens of project stores hold their own `identity/user.md`, candidate queue and audit log; the same candidate
id (UC1) exists in most of them. The central profile (`identity_store.py`) is active only once `<store>/identity/profile.md`
exists. This script builds that profile from the project files - 1:1, nothing merged (owner decision 2026-10-28:
take every line over unchanged; the data analysis found no pair of lines from two stores with Jaccard >= 0.8, similar
pairs are only reported) - and writes `profile.md` LAST, so a half-finished run stays inactive.

Phases (each its own command):
  plan    read only: finds the stores like the owner's store manifest (depth 6, same skip dirs and copy markers, no
          junctions; a folder that cannot be listed stops the run), reads every profile line losslessly (class,
          section, raw text, line ending), gives each candidate a global id PCn with `origin {projekt, id[, datei]}`,
          maps a line's end citation "(UCn, date)" to "(PCm, date)", renames a key that an earlier store already uses
          to "<key>@<projekt>", marks an old promoted/confirmed candidate without `confirmed_by` as "legacy" (Q14) and
          sets no artificial session (A3: the adoption rule of apply_wrapup counts the first real session). The plan
          file holds the sources, the complete target and the counts.
  backup / apply / verify   see their own docstrings (Tasks 10 and 11).

The sources are never written (D-021). Exit codes: 0 = done, 1 = usage error, 2 = stopped before writing (gap,
unreadable file, existing target) - nothing was written.
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import hashlib
import json
import os
import re
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FORMAT = 1
SKIP_DIRS = {"node_modules", ".git", "AppData", "__pycache__", ".venv", "venv", "site-packages", ".cache",
             "Pictures", "Videos", "Music"}
# copies and probes, no real stores (the same list the owner's store manifest uses)
COPY_MARKERS = ("/.claude/plugins/cache", "/ai/worktrees", "agent-memory-atlas-runtime-", "/tests/fixtures",
                "nested-agent-memory-quarantine", "/scripts/state/work/loop-", "agent-lab-builds-proof")
MAX_DEPTH = 6
STORE = ".agent-memory"
USER_MD = "identity/user.md"
CHANGELOG = "identity/user-changelog.json"
QUEUE = "working/user-candidates.json"
QUEUE_FILES = (QUEUE, "identity/user-candidates.json")  # the second one strayed into identity/ in one store
SOURCE_FILES = (USER_MD, CHANGELOG) + QUEUE_FILES
TOP_SECTIONS = ("Preferences", "Work Style", "Known Corrections")
PROFILE_TITLE = "# User Profile"
SIMILAR_MIN = 0.8

_PLACEHOLDER = re.compile(r"^[-*]\s*\((?:Will be populated|Recorded when|Weiteres wird|Wird erst nach)", re.IGNORECASE)
_PAREN_ONLY = re.compile(r"[-*]\s*\([^()]*\)")
_NO_CANDIDATES = "*Keine offenen Kandidaten.*"
_INITIALIZED = re.compile(r"\*Initialized: [^*]*\*")
_BULLET = re.compile(r"^\s*[-*] ")
# the citation at the END of a line, optionally followed by more bracket groups: "(UC3, 2026-07-01)", "[UC1, confirmed]"
# one citation-like bracket group, and the run of bracket groups that ends a line
_CITATION_GROUP = re.compile(r"[\[(]\s*(?P<id>[A-Za-z]{1,6}-?\d{1,6})\s*,[^\[\]()]*[\])]")
_TRAILING_GROUPS = re.compile(r"(?:\s*[\[(][^\[\]()]*[\])])+\s*$")
_CITATION_ANY_END = re.compile(r"\s*[\[(]\s*[A-Za-z]{1,6}-?\d{1,6}\s*,[^\[\]()]*[\])](?:\s*[\[(][^\[\]()]*[\])])*\s*$")


class Stop(Exception):
    """The run stops before writing anything (exit 2)."""


def log(text: str = "") -> None:
    print(text, file=sys.stderr, flush=True)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------------------------------------- finding the stores

def _is_copy(rel: str) -> bool:
    p = "/" + rel.replace("\\", "/").lower()
    return any(m in p for m in COPY_MARKERS)


def _is_junction(e: os.DirEntry) -> bool:
    return bool(getattr(e, "is_junction", lambda: False)())


def find_stores(home: str, gaps: list | None = None) -> list[str]:
    """Relative paths ("/") of every .agent-memory under home, without copies and junctions, sorted. A folder that
    cannot be listed goes to `gaps` (a store inside it would be invisible)."""
    found, stack = [], [(home, 0)]
    while stack:
        path, depth = stack.pop()
        try:
            entries = list(os.scandir(path))
        except OSError as ex:
            if gaps is not None:
                gaps.append(f"{path}: cannot be listed ({ex.__class__.__name__})")
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False) or _is_junction(e):
                    continue
            except OSError as ex:
                if gaps is not None:
                    gaps.append(f"{e.path}: cannot be checked ({ex.__class__.__name__})")
                continue
            if e.name == STORE:
                rel = os.path.relpath(e.path, home).replace("\\", "/")
                if not _is_copy(rel):
                    found.append(rel)
                continue
            if e.name in SKIP_DIRS or (e.name.startswith(".") and e.name != ".claude"):
                continue
            if depth < MAX_DEPTH:
                stack.append((e.path, depth + 1))
    return sorted(found)


def project_of(rel_store: str) -> str:
    """Origin key of a store: its project folder relative to home ("~" for the home store) - the same key
    identity_store.project_key gives a store at run time."""
    parent = rel_store.rsplit("/", 1)[0] if "/" in rel_store else ""
    return parent or "~"


# ------------------------------------------------------------------------------------------------- reading a profile

def parse_profile(text: str, origin: dict) -> list[dict]:
    """Every non-blank line of a profile as {origin {projekt, datei, zeile}, klasse, sektion, roh, eol, vorlage}.
    Classes (the counting rule of the store manifest): titel "# ", sektion "## ", platzhalter, bullet ("- "/"* "),
    fortsetzung (text directly under a bullet or a continuation), praeambel (any other text). `roh` is the line
    without its ending and without a leading BOM; `eol` is "\\r\\n", "\\n" or "" (last line without an ending)."""
    if text.startswith("﻿"):
        text = text[1:]
    rows, section, before = [], None, None
    parts = text.split("\n")
    for nr, raw in enumerate(parts, 1):
        last = nr == len(parts)
        if last and raw == "":
            break
        line = raw[:-1] if raw.endswith("\r") else raw
        eol = "" if last else ("\r\n" if raw.endswith("\r") else "\n")
        s = line.strip()
        if not s:
            before = None
            continue
        if line.startswith("## "):
            section, before, cls = line[3:].strip(), None, "sektion"
        elif line.startswith("# "):
            section, before, cls = None, None, "titel"
        else:
            if _PLACEHOLDER.match(s) or _PAREN_ONLY.fullmatch(s) or s == _NO_CANDIDATES:
                cls = "platzhalter"
            elif _BULLET.match(line) and len(s) > 1:
                cls = "bullet"
            elif before in ("bullet", "fortsetzung"):
                cls = "fortsetzung"
            else:
                cls = "praeambel"
            before = cls
        rows.append({"origin": dict(origin, zeile=nr), "klasse": cls, "sektion": section, "roh": line, "eol": eol,
                     "vorlage": bool(_INITIALIZED.fullmatch(s))})
    return rows


def _source_exists(path: str) -> bool:
    """True for a regular file, False when it is absent; any other problem (no permission, not a file) stops the run
    - os.path.isfile would read such a source as 'missing' and the migration would silently skip it (Codex F12)."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return False
    except OSError as ex:
        raise Stop(f"{path} cannot be checked ({ex.__class__.__name__}) - nothing was written")
    if not stat.S_ISREG(st.st_mode):
        raise Stop(f"{path} is not a regular file - nothing was written")
    return True


def _read_json_list(path: str, key: str) -> list:
    with open(path, "rb") as fh:
        data = json.loads(fh.read().decode("utf-8-sig"))
    if isinstance(data, dict) and isinstance(data.get(key), list):
        data = data[key]
    if not isinstance(data, list):
        raise Stop(f"{path}: neither a list nor an object with '{key}'")
    bad = [i for i, r in enumerate(data) if not isinstance(r, dict)]
    if bad:
        raise Stop(f"{path}: entries {bad[:5]} are not objects - they cannot carry an origin")
    return data


def read_store(home: str, rel: str) -> dict:
    """Everything the migration takes from one store, read only. Unreadable -> Stop."""
    root = os.path.join(home, *rel.split("/"))
    projekt = project_of(rel)
    out = {"store": rel, "projekt": projekt, "dateien": {}, "zeilen": [], "kandidaten": [], "kandidaten_datei": [],
           "changelog": []}
    for name in SOURCE_FILES:
        path = os.path.join(root, *name.split("/"))
        if not _source_exists(path):
            continue
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            out["dateien"][name] = sha256(data)
            if name == USER_MD:
                out["zeilen"] = parse_profile(data.decode("utf-8"), {"projekt": projekt, "datei": name})
            elif name == CHANGELOG:
                out["changelog"] = _read_json_list(path, "entries")
            else:
                rows = _read_json_list(path, "candidates")
                out["kandidaten"] += rows
                out["kandidaten_datei"] += [name] * len(rows)  # beside, never inside the row (Codex F16)
        except (OSError, UnicodeDecodeError, ValueError) as ex:
            raise Stop(f"{rel}/{name} is unreadable ({ex.__class__.__name__}: {ex}) - nothing was written")
    return out


# ------------------------------------------------------------------------------------------------- building the target

def _norm_tokens(text: str) -> set:
    return set(re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower()).split())


def _jaccard(a: str, b: str) -> float:
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def map_citations(line: str, ids: dict, ambiguous: set) -> tuple[str, dict | None]:
    """(line with its citation mapped, record or None). The citation is a candidate id among the bracket groups that
    END the line; the last one that names a candidate of this store wins, so '(UC1, d) (v1, final)' maps UC1 and keeps
    the note (Codex F06). An id that occurs twice in the store is ambiguous: the line keeps it, no guessed link (F04)."""
    m = _TRAILING_GROUPS.search(line)
    groups = list(_CITATION_GROUP.finditer(line, m.start())) if m else []
    if not groups:
        return line, None
    for g in reversed(groups):  # the LAST group that names a candidate of this store decides, ambiguous or not (R2-01)
        if g.group("id") in ids:
            new = ids[g.group("id")]
            return line[:g.start("id")] + new + line[g.end("id"):], {"alt": g.group("id"), "neu": new}
        if g.group("id") in ambiguous:
            return line, {"alt": g.group("id"), "neu": None, "grund": "mehrdeutig"}
    return line, {"alt": groups[-1].group("id"), "neu": None, "grund": "ohne_kandidat"}


def build_plan(home: str, now: str) -> dict:
    gaps: list = []
    stores_rel = find_stores(home, gaps)
    if gaps:
        raise Stop("folders could not be searched, a store inside would be missed: " + "; ".join(gaps[:5]))
    stores = [read_store(home, rel) for rel in stores_rel]

    kandidaten, kollisionen, hinweise, used_keys, zuordnung = [], [], [], set(), []
    id_maps, ambiguous = {}, {}
    for st in stores:
        ids = id_maps.setdefault(st["store"], {})
        amb = ambiguous.setdefault(st["store"], set())
        for c, datei in zip(st["kandidaten"], st["kandidaten_datei"]):
            new = dict(c)
            pc = f"PC{len(kandidaten) + 1}"
            old = c.get("id")
            new["id"] = pc
            origin = {"projekt": st["projekt"], "id": old}
            if datei != QUEUE:
                origin["datei"] = datei
                hinweise.append(f"{st['store']}/{datei}: candidate {old} taken over from a stray queue file")
            if isinstance(old, str) and old:
                if old in ids or old in amb:
                    hinweise.append(f"{st['store']}: candidate id {old} occurs twice - a citation of it stays as it is "
                                    f"(mehrdeutig, no guessed link)")
                    ids.pop(old, None)
                    amb.add(old)
                else:
                    ids[old] = pc
            key = c.get("key")
            if isinstance(key, str) and key:
                if key in used_keys:
                    alt, n = f"{key}@{st['projekt']}", 2
                    while alt in used_keys:
                        alt, n = f"{key}@{st['projekt']}#{n}", n + 1
                    kollisionen.append({"key": key, "projekt": st["projekt"], "neu_key": alt, "id": pc})
                    new["key"] = alt
                used_keys.add(new["key"])
            if new.get("status") in ("promoted", "confirmed") and not new.get("confirmed_by"):
                new["confirmed_by"] = "legacy"  # Q14: confirmed before the quote duty existed - visible, not hidden
            new["origin"] = origin
            kandidaten.append(new)

    # profile lines: preamble area first, then the template sections, then every other section in first-seen order
    blocks = collections.OrderedDict()  # section -> [list of rows of one block]
    ledger_rows, counts = [], collections.Counter()
    for sec in (None,) + TOP_SECTIONS:
        blocks[sec] = []
    for st in stores:
        current = None
        for row in st["zeilen"]:
            cls = row["klasse"]
            if cls in ("titel", "sektion"):
                current = None
                continue
            entry = {k: row[k] for k in ("origin", "klasse", "sektion", "roh", "eol")}
            if cls == "platzhalter" or row["vorlage"]:
                counts["platzhalter" if cls == "platzhalter" else "vorlage"] += 1
                entry.update(entscheidung="vorlage", ziel=None)
                ledger_rows.append(entry)
                current = None
                continue
            # bullets, continuation and own preamble lines alike: 5 real continuation lines end with a citation (F05)
            text, rec = map_citations(row["roh"], id_maps.get(st["store"], {}), ambiguous.get(st["store"], set()))
            if rec:
                zuordnung.append(dict(rec, projekt=st["projekt"], zeile=row["origin"]["zeile"]))
            entry.update(entscheidung="uebernommen", ziel=text)
            ledger_rows.append(entry)
            counts["praeambel_eigen" if cls == "praeambel" else cls] += 1
            if cls == "fortsetzung" and current is not None:
                current.append((cls, text, row))
            else:
                current = [(cls, text, row)]
                blocks.setdefault(row["sektion"], []).append(current)
    lines = [PROFILE_TITLE, ""]
    for sec, sec_blocks in blocks.items():
        if not sec_blocks:
            continue
        if sec is not None:
            if lines[-1] != "":
                lines.append("")
            lines += [f"## {sec}"]
        for block in sec_blocks:
            if block[0][0] == "praeambel" and lines[-1] != "":
                lines.append("")  # a preamble line stays preamble only after a blank line or a heading
            lines += [text for _cls, text, _row in block]
            if block[0][0] == "praeambel":
                lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    profile_md = "\n".join(lines) + "\n"

    changelog = []
    for st in stores:
        for i, e in enumerate(st["changelog"]):
            new = dict(e)
            origin = {"projekt": st["projekt"], "datei": CHANGELOG, "eintrag": i}
            if "origin" in e:
                origin["quelle"] = e["origin"]  # the entry's own origin, nested - no field of the source is touched (F17)
            new["origin"] = origin
            changelog.append(new)
    for r in ledger_rows:
        if r["entscheidung"] == "uebernommen":
            changelog.append({"ts": now, "field": "profile.md/migration", "old_value": None, "new_value": r["ziel"],
                              "quelle": r["roh"], "origin": r["origin"]})

    bullets = [(r["roh"], r["origin"]) for r in ledger_rows if r["klasse"] == "bullet" and r["entscheidung"] == "uebernommen"]
    similar = []
    for i in range(len(bullets)):
        for j in range(i + 1, len(bullets)):
            (a, oa), (b, ob) = bullets[i], bullets[j]
            if oa["projekt"] == ob["projekt"]:
                continue
            x = _jaccard(_CITATION_ANY_END.sub("", a), _CITATION_ANY_END.sub("", b))
            if x >= SIMILAR_MIN:
                similar.append({"a": a, "b": b, "jaccard": round(x, 3), "origin_a": oa, "origin_b": ob})

    status = collections.Counter(str(c.get("status")) for c in kandidaten)
    zaehler = {"stores": len(stores), "profile_mit_inhalt": sum(
                   1 for st in stores if any(r["entscheidung"] == "uebernommen" and r["origin"]["projekt"] == st["projekt"]
                                             for r in ledger_rows)),
               "bullet": counts["bullet"], "fortsetzung": counts["fortsetzung"], "praeambel_eigen": counts["praeambel_eigen"],
               "vorlage": counts["vorlage"], "platzhalter": counts["platzhalter"], "kandidaten": len(kandidaten),
               "status": dict(sorted(status.items())), "changelog": sum(len(st["changelog"]) for st in stores),
               "zitierungen_abgebildet": sum(1 for z in zuordnung if z["neu"]),
               "zitierungen_ohne_kandidat": sum(1 for z in zuordnung if not z["neu"]),
               "kollisionen": len(kollisionen), "aehnliche_paare": len(similar), "zusammengefuehrt": 0,
               "legacy_markiert": sum(1 for c in kandidaten if c.get("confirmed_by") == "legacy")}
    ledger = {"format": FORMAT, "erzeugt": now, "zeilen": ledger_rows, "zuordnung": zuordnung,
              "kollisionen": kollisionen, "aehnliche_paare": similar, "zaehler": zaehler}
    return {"format": FORMAT, "erzeugt": now, "home": os.path.abspath(home), "stores": stores,
            "ziel": {"profile_md": profile_md, "kandidaten": kandidaten, "changelog": changelog, "ledger": ledger},
            "zuordnung": zuordnung, "kollisionen": kollisionen, "aehnliche_paare": similar, "zaehler": zaehler,
            "hinweise": hinweise}


# ------------------------------------------------------------------------------------------------- commands

def _inside_store(path: str) -> bool:
    """True when the path lies in a store, also in another spelling (Windows ignores case) or via a link (F13)."""
    parts = os.path.normcase(os.path.realpath(os.path.abspath(path))).replace("\\", "/").split("/")
    return os.path.normcase(STORE) in parts


def cmd_plan(args) -> int:
    if os.path.exists(args.out):
        log(f"plan: {args.out} exists - choose a new file (a plan is never overwritten)")
        return 2
    if _inside_store(args.out):
        log(f"plan: {args.out} lies inside a store - write the plan outside every .agent-memory")
        return 2
    now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    plan = build_plan(args.home, now)
    with open(args.out, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(plan, ensure_ascii=False, indent=1) + "\n")
    z = plan["zaehler"]
    print(f"Plan {args.out}: {z['stores']} Stores, {z['profile_mit_inhalt']} Profile mit Inhalt, {z['bullet']} Zeilen, "
          f"{z['fortsetzung']} Fortsetzungen, {z['kandidaten']} Kandidaten {z['status']}, {z['changelog']} "
          f"Changelog-Eintraege, {z['kollisionen']} Key-Kollisionen, {z['aehnliche_paare']} aehnliche Paare "
          f"(nicht zusammengefuehrt), {z['zitierungen_ohne_kandidat']} Zitierungen ohne Kandidat")
    for h in plan["hinweise"]:
        print(f"  Hinweis: {h}")
    return 0


# ------------------------------------------------------------------------------------------------- backup and apply

BACKUP_DIR_NAME = "agent-memory.kopie"  # not ".agent-memory": no store scanner may take the copy for a real store
FAIL_ENV = "AGENTIC_OS_MIGRATE_FAIL_AFTER"  # crash probe (E7); honoured only for a central store in the temp folder


def _load_plan(path: str) -> tuple[dict, str]:
    with open(path, "rb") as fh:
        data = fh.read()
    plan = json.loads(data.decode("utf-8"))
    if plan.get("format") != FORMAT or "ziel" not in plan or "stores" not in plan:
        raise Stop(f"{path} is not a migration plan of format {FORMAT}")
    return plan, sha256(data)


def _source_state(home: str, plan: dict) -> list[str]:
    """Differences between the sources now and when the plan was made (empty = unchanged). A new store, a missing one,
    and any added, removed or changed source file count."""
    diffs = []
    gaps: list = []
    now_stores = find_stores(home, gaps)
    if gaps:
        diffs += gaps[:5]
    planned = [st["store"] for st in plan["stores"]]
    if now_stores != planned:
        diffs.append(f"stores differ: new {sorted(set(now_stores) - set(planned))[:5]}, "
                     f"gone {sorted(set(planned) - set(now_stores))[:5]}")
    for st in plan["stores"]:
        root = os.path.join(home, *st["store"].split("/"))
        for name in SOURCE_FILES:
            path = os.path.join(root, *name.split("/"))
            want = st["dateien"].get(name)
            have = None
            if _source_exists(path):
                with open(path, "rb") as fh:
                    have = sha256(fh.read())
            if have != want:
                diffs.append(f"{st['store']}/{name}: {'changed' if have and want else 'added' if have else 'removed'}")
    return diffs


def cmd_backup(args) -> int:
    plan, plan_sha = _load_plan(args.plan)
    if os.path.exists(args.out) or _inside_store(args.out):
        log(f"backup: {args.out} exists or lies inside a store - choose a new folder outside every .agent-memory")
        return 2
    diffs = _source_state(plan["home"], plan)
    if diffs:
        raise Stop("the sources changed since the plan - run plan again: " + "; ".join(diffs[:5]))
    manifest = {"format": FORMAT, "plan_sha256": plan_sha, "home": plan["home"], "dateien": {}}
    for st in plan["stores"]:
        src_root = os.path.join(plan["home"], *st["store"].split("/"))
        dst_root = os.path.join(args.out, "kopie", *st["store"].split("/")[:-1], BACKUP_DIR_NAME)
        files = {}
        candidates = [QUEUE]
        ident = os.path.join(src_root, "identity")
        if os.path.isdir(ident):
            for base, _dirs, names in os.walk(ident):
                for n in names:
                    candidates.append(os.path.relpath(os.path.join(base, n), src_root).replace(os.sep, "/"))
        for rel in sorted(set(candidates)):
            src = os.path.join(src_root, *rel.split("/"))
            if not os.path.isfile(src):
                continue
            with open(src, "rb") as fh:
                data = fh.read()
            dst = os.path.join(dst_root, *rel.split("/"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "xb") as fh:
                fh.write(data)
            files[rel] = sha256(data)
        manifest["dateien"][st["store"]] = files
    with open(os.path.join(args.out, "backup.json"), "x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    print(f"Backup {args.out}: {sum(len(f) for f in manifest['dateien'].values())} Dateien aus "
          f"{len(manifest['dateien'])} Stores")
    return 0


def _targets(plan: dict, plan_sha: str) -> list[tuple[str, bytes]]:
    """The central files in write order; profile.md LAST - it is the switch that activates the central profile."""
    import identity_briefing
    import identity_store
    z = plan["ziel"]

    def js(obj):
        return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    ledger = dict(z["ledger"], plan_sha256=plan_sha, status="abgeschlossen")
    briefing = "".join(line + "\n" for line in identity_briefing.select_lines(z["profile_md"], z["kandidaten"]))
    return [(identity_store.PROFILE_CHANGELOG, js(z["changelog"])),
            (identity_store.PROFILE_QUEUE, js(z["kandidaten"])),
            (identity_store.MIGRATION_LEDGER, js(ledger)),
            (identity_store.PROFILE_BRIEFING, briefing.encode("utf-8")),
            (identity_store.PROFILE_MD, z["profile_md"].encode("utf-8"))]


def _write_atomic(path: str, data: bytes) -> None:
    """A fresh, exclusively created temp file next to the target, then os.replace - a fixed temp name could already
    exist as a hardlink to a source and be written through (Codex F03)."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _check_backup(plan: dict, backup_dir: str, backup: dict) -> list[str]:
    """The backup must hold every planned source file with the plan's hash, and every copy must be intact - a damaged
    backup is no way back, so apply must not switch the central profile on with it (Codex F01)."""
    bad = []
    for st in plan["stores"]:
        rec = (backup.get("dateien") or {}).get(st["store"])
        if rec is None:
            bad.append(f"{st['store']}: missing in the backup")
            continue
        for name, digest in st["dateien"].items():
            if rec.get(name) != digest:
                bad.append(f"{st['store']}/{name}: backup hash differs from the plan")
        for rel, digest in rec.items():
            try:
                with open(_backup_path(backup_dir, st["store"], rel), "rb") as fh:
                    ok = sha256(fh.read()) == digest
            except OSError:
                ok = False
            if not ok:
                bad.append(f"{st['store']}/{rel}: backup copy missing or damaged")
    return bad


def cmd_apply(args) -> int:
    import tempfile

    import identity_store
    import store_lock
    plan, plan_sha = _load_plan(args.plan)
    with open(os.path.join(args.backup, "backup.json"), encoding="utf-8") as fh:
        backup = json.load(fh)
    if backup.get("plan_sha256") != plan_sha:
        raise Stop(f"backup {args.backup} belongs to another plan - make a backup of THIS plan first")
    store, source = identity_store.resolve_store(args.identity_store, os.environ)
    if source == "home" and not identity_store.same_store(store, os.path.join(plan["home"], STORE)):
        raise Stop(f"the central store {store} (found via the home folder) is not the home store of the plan "
                   f"({plan['home']}) - pass --identity-store explicitly")
    damaged = _check_backup(plan, args.backup, backup)
    if damaged:
        raise Stop("the backup is incomplete or damaged - make a new backup: " + "; ".join(damaged[:5]))
    diffs = _source_state(plan["home"], plan)
    if diffs:
        raise Stop("the sources changed since the plan and backup - run plan and backup again: " + "; ".join(diffs[:5]))
    targets = _targets(plan, plan_sha)
    paths = [(rel, os.path.join(store, *rel.split("/")), data) for rel, data in targets]
    md_rel = identity_store.PROFILE_MD

    def central_state():
        """({rel: already the target bytes}, finished). Raises Stop for a foreign central profile."""
        same = {rel: os.path.isfile(p) and open(p, "rb").read() == data for rel, p, data in paths}
        if os.path.isfile(os.path.join(store, *md_rel.split("/"))):
            if all(same.values()):
                return same, True
            raise Stop(f"{md_rel} exists in {store} but does not match this plan's finished migration - the central "
                       f"profile is already active; nothing was written")
        foreign = [rel for rel, p, _d in paths if os.path.isfile(p) and not same[rel]]
        if foreign:
            raise Stop(f"{store} already holds {foreign} with other content than this plan - remove them (a backup of "
                       f"the sources exists) or check what wrote them; nothing was written")
        return same, False

    same, finished = central_state()
    if finished:
        print(f"Zentral {store}: bereits migriert mit diesem Plan - nichts zu tun")
        return 0
    if args.dry_run:
        for rel, _p, data in paths:
            print(f"wuerde schreiben {rel} ({len(data)} Bytes)")
        return 0
    fail_after = None
    if os.environ.get(FAIL_ENV) and identity_store._under(store, tempfile.gettempdir()):
        fail_after = int(os.environ[FAIL_ENV])
    with store_lock.store_lock(store, timeout=args.lock_timeout, lock_rel=identity_store.PROFILE_LOCK):
        # again under the lock: another writer may have created central files while this run waited (Codex F02)
        same, finished = central_state()
        if finished:
            print(f"Zentral {store}: bereits migriert mit diesem Plan - nichts zu tun")
            return 0
        for n, (rel, p, data) in enumerate(paths):
            if fail_after is not None and n >= fail_after:
                raise OSError(f"simulated crash after {fail_after} writes ({FAIL_ENV})")
            if not same[rel]:
                _write_atomic(p, data)
            print(f"geschrieben {rel}", flush=True)
    z = plan["zaehler"]
    print(f"Zentral {store} ist aktiv: {z['bullet']} Zeilen, {z['fortsetzung']} Fortsetzungen, {z['kandidaten']} "
          f"Kandidaten, {len(plan['ziel']['changelog'])} Changelog-Eintraege")
    return 0


# ------------------------------------------------------------------------------------------------- verify

def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _backup_path(backup_dir: str, store_rel: str, rel: str) -> str:
    return os.path.join(backup_dir, "kopie", *store_rel.split("/")[:-1], BACKUP_DIR_NAME, *rel.split("/"))


def verify_migration(plan: dict, backup_dir: str, backup: dict, store: str) -> dict:
    """Recount from the BACKUP bytes, independent of the plan's target (a faulty plan must not pass):
    E2 every content line of every user.md stands in profile.md with its class and EXACTLY the citation that the
       central queue's origins give it (independent of the ledger, Codex F07); template lines and placeholders stand
       in the ledger with their text and origin (F10);
    E3 the candidates are the source candidates (same fields; only id, key suffix "@<projekt>", origin and Q14's
       confirmed_by "legacy" differ), every central id is a unique PCn and every key unique (F08, F09);
    E4 every source changelog entry stands in the central changelog with its origin;
    the briefing projection exists and equals the one of profile.md and the queue (T-050);
    and every source file is byte-equal to the backup (D-021), and no store appeared since the plan (F11)."""
    import identity_store
    bad, counts = [], collections.Counter()

    def read_central(rel, as_json):
        path = os.path.join(store, *rel.split("/"))
        if not os.path.isfile(path):
            bad.append(f"{rel} fehlt im Zentral {store}")
            return [] if as_json else ""
        with open(path, "rb") as fh:
            data = fh.read().decode("utf-8")
        return json.loads(data) if as_json else data

    profile = read_central(identity_store.PROFILE_MD, False)
    queue = read_central(identity_store.PROFILE_QUEUE, True)
    changelog = read_central(identity_store.PROFILE_CHANGELOG, True)
    ledger = read_central(identity_store.MIGRATION_LEDGER, True) or {}
    # the briefing projection the hook shows must be the one of the profile and queue on disk (T-050)
    import identity_briefing
    briefing = read_central(identity_store.PROFILE_BRIEFING, False)
    want = "".join(line + "\n" for line in identity_briefing.select_lines(
        profile, queue if isinstance(queue, list) else []))
    if os.path.isfile(os.path.join(store, *identity_store.PROFILE_BRIEFING.split("/"))) and briefing != want:
        bad.append(f"{identity_store.PROFILE_BRIEFING} ist nicht die Projektion von profile.md und Queue "
                   f"(neu erzeugen: ein Wrap-up mit Identitaetsdaten schreibt sie)")

    # no store appeared or vanished since the plan (F11)
    gaps: list = []
    now_stores = find_stores(plan["home"], gaps)
    planned = [st["store"] for st in plan["stores"]]
    for g in gaps[:5]:
        bad.append(f"Quellen nicht vollstaendig durchsuchbar: {g}")
    for rel in sorted(set(now_stores) - set(planned)):
        new_root = os.path.join(plan["home"], *rel.split("/"))
        if identity_store.same_store(new_root, store):
            # the central store itself, created by apply when the home store did not exist yet: it may hold the
            # central files, but no project source the migration never saw (Codex R2-02)
            for name in SOURCE_FILES:
                if _source_exists(os.path.join(new_root, *name.split("/"))):
                    bad.append(f"Quelle {rel}/{name} ist im neu angelegten Zentral-Store entstanden und nicht migriert")
            continue
        bad.append(f"Store {rel} ist nach dem Plan entstanden und nicht migriert")
    for rel in sorted(set(planned) - set(now_stores)):
        bad.append(f"Store {rel} aus dem Plan ist verschwunden")

    # sources unchanged since the backup, and the backup itself intact
    for st in plan["stores"]:
        recorded = backup["dateien"].get(st["store"], {})
        root = os.path.join(plan["home"], *st["store"].split("/"))
        for rel, digest in recorded.items():
            cur = os.path.join(root, *rel.split("/"))
            have = None
            if _source_exists(cur):
                with open(cur, "rb") as fh:
                    have = sha256(fh.read())
            if have != digest:
                bad.append(f"Quelle {st['store']}/{rel} ist nicht mehr byte-gleich zum Backup")
            with open(_backup_path(backup_dir, st["store"], rel), "rb") as fh:
                if sha256(fh.read()) != digest:
                    bad.append(f"Backup-Kopie {st['store']}/{rel} ist beschaedigt")
        for name in SOURCE_FILES:
            if name not in recorded and _source_exists(os.path.join(root, *name.split("/"))):
                bad.append(f"Quelle {st['store']}/{name} ist nach dem Backup entstanden")

    def backup_bytes(st, rel):
        if rel not in backup["dateien"].get(st["store"], {}):
            return None
        with open(_backup_path(backup_dir, st["store"], rel), "rb") as fh:
            return fh.read()

    # E2 lines. The expected citation of a line comes from the central queue's origins (projekt, old id), never from
    # the ledger: a profile and a ledger that are wrong together must not pass (F07).
    pcs = collections.defaultdict(list)
    for c in queue:
        o = c.get("origin") or {} if isinstance(c, dict) else {}
        if isinstance(o.get("id"), str) and o.get("id"):
            pcs[(o.get("projekt"), o["id"])].append(c.get("id"))
    want, want_strip, sources, tpl_src = collections.Counter(), collections.Counter(), {}, collections.Counter()
    for st in plan["stores"]:
        data = backup_bytes(st, USER_MD)
        if data is None:
            continue
        ids = {old: v[0] for (proj, old), v in pcs.items() if proj == st["projekt"] and len(v) == 1}
        amb = {old for (proj, old), v in pcs.items() if proj == st["projekt"] and len(v) > 1}
        for row in parse_profile(data.decode("utf-8"), {"projekt": st["projekt"], "datei": USER_MD}):
            cls = row["klasse"]
            if cls in ("titel", "sektion"):
                continue
            if cls == "platzhalter" or row["vorlage"]:
                counts["platzhalter" if cls == "platzhalter" else "vorlage"] += 1
                tpl_src[_canon([row["origin"], cls, row["roh"], row["eol"]])] += 1
                continue
            counts["praeambel_eigen" if cls == "praeambel" else cls] += 1
            expected, _rec = map_citations(row["roh"], ids, amb)
            want[(cls, expected)] += 1
            want_strip[(cls, _CITATION_ANY_END.sub("", row["roh"]))] += 1
            sources.setdefault((cls, expected), f"{st['projekt']}:{row['origin']['zeile']}")
    rows_central = [r for r in parse_profile(profile, {"projekt": "zentral", "datei": "profile.md"})
                    if r["klasse"] not in ("titel", "sektion")]
    have = collections.Counter((r["klasse"], r["roh"]) for r in rows_central)
    have_strip = collections.Counter((r["klasse"], _CITATION_ANY_END.sub("", r["roh"])) for r in rows_central)
    for k, n in (want - have).items():
        strip = (k[0], _CITATION_ANY_END.sub("", k[1]))
        if have_strip.get(strip, 0) >= want_strip.get(strip, 0):
            bad.append(f"Zitierung falsch ({k[0]}, {n}x, Quelle {sources[k]}): erwartet in profile.md '{k[1]}'")
        else:
            bad.append(f"Zeile fehlt in profile.md ({k[0]}, {n}x, Quelle {sources[k]}): {k[1]}")
    for k, n in (have - want).items():
        bad.append(f"Zeile in profile.md ohne Quelle oder mit falscher Zitierung ({k[0]}, {n}x): {k[1]}")
    rows = ledger.get("zeilen") or []
    # template lines and placeholders live only in the ledger: their text and origin are checked, not their count (F10)
    tpl_led = collections.Counter(_canon([r.get("origin"), r.get("klasse"), r.get("roh"), r.get("eol")])
                                  for r in rows if r.get("entscheidung") == "vorlage")
    if tpl_led != tpl_src:
        bad.append(f"Ledger: {sum((tpl_src - tpl_led).values())} Vorlage-/Platzhalterzeilen der Quellen fehlen oder sind "
                   f"veraendert, {sum((tpl_led - tpl_src).values())} stehen ohne Quelle im Ledger")

    # E3 candidates
    src_c = collections.Counter()
    labels = {}
    for st in plan["stores"]:
        for name in QUEUE_FILES:
            data = backup_bytes(st, name)
            if data is None:
                continue
            for c in _read_json_list(_backup_path(backup_dir, st["store"], name), "candidates"):
                norm = {k: v for k, v in c.items() if k not in ("id", "key")}
                if norm.get("confirmed_by") == "legacy" and norm.get("status") in ("promoted", "confirmed"):
                    norm.pop("confirmed_by")  # the same normalisation as for the central row (F15)
                norm["_origin"] = [st["projekt"], c.get("id"), None if name == QUEUE else name]
                norm["_key"] = c.get("key")
                key = _canon(norm)
                src_c[key] += 1
                labels[key] = f"{st['projekt']}/{c.get('id')}"
                counts["kandidaten"] += 1
                counts["status:" + str(c.get("status"))] += 1
    ids_seen, keys_seen = collections.Counter(c.get("id") for c in queue), collections.Counter(
        c.get("key") for c in queue if isinstance(c.get("key"), str) and c.get("key"))
    for cid, n in ids_seen.items():
        if not (isinstance(cid, str) and re.fullmatch(r"PC\d+", cid)):
            bad.append(f"Zentral-Id {cid!r} ist keine PC-Id")
        elif n > 1:
            bad.append(f"Zentral-Id {cid} kommt {n}x vor")
    for key, n in keys_seen.items():
        if n > 1:
            bad.append(f"Key {key!r} kommt im Zentral {n}x vor (Kollision nicht aufgeloest)")
    dst_c = collections.Counter()
    for t in queue:
        o = t.get("origin") or {}
        norm = {k: v for k, v in t.items() if k not in ("id", "key", "origin")}
        if norm.get("status") in ("promoted", "confirmed") and not norm.get("confirmed_by"):
            bad.append(f"Kandidat {t.get('id')} ({o.get('projekt')}/{o.get('id')}) ist {norm.get('status')} ohne "
                       f"confirmed_by (Q14 verlangt legacy)")
        if norm.get("confirmed_by") == "legacy" and norm.get("status") in ("promoted", "confirmed"):
            norm.pop("confirmed_by")
        norm["_origin"] = [o.get("projekt"), o.get("id"), o.get("datei")]
        key = t.get("key")
        if isinstance(key, str) and o.get("projekt"):
            key = re.sub("@" + re.escape(o["projekt"]) + r"(#\d+)?$", "", key)
        norm["_key"] = key
        k = _canon(norm)
        dst_c[k] += 1
        labels.setdefault(k, f"{o.get('projekt')}/{o.get('id')} -> {t.get('id')}")
    for k, n in (src_c - dst_c).items():
        bad.append(f"Kandidat fehlt oder ist veraendert im Zentral: {labels[k]} ({n}x)")
    for k, n in (dst_c - src_c).items():
        bad.append(f"Kandidat im Zentral ohne gleiche Quelle: {labels[k]} ({n}x)")

    # E4 changelog
    central_src = {}
    for e in changelog:
        o = e.get("origin") or {}
        if o.get("datei") == CHANGELOG:
            d = {k: v for k, v in e.items() if k != "origin"}
            if "quelle" in o:
                d["origin"] = o["quelle"]
            central_src[(o.get("projekt"), o.get("eintrag"))] = _canon(d)
    for st in plan["stores"]:
        if backup_bytes(st, CHANGELOG) is None:
            continue
        for i, e in enumerate(_read_json_list(_backup_path(backup_dir, st["store"], CHANGELOG), "entries")):
            counts["changelog"] += 1
            if central_src.get((st["projekt"], i)) != _canon(e):
                bad.append(f"Changelog-Eintrag {st['projekt']} #{i} fehlt im Zentral-Changelog oder ist veraendert")
    migr = sum(1 for e in changelog if e.get("field") == "profile.md/migration")
    if migr != sum(1 for r in rows if r.get("entscheidung") == "uebernommen"):
        bad.append(f"Changelog: {migr} Eintraege profile.md/migration, aber "
                   f"{sum(1 for r in rows if r.get('entscheidung') == 'uebernommen')} uebernommene Zeilen im Ledger")
    if ledger.get("status") != "abgeschlossen":
        bad.append("Ledger ist nicht als abgeschlossen markiert")
    return {"ok": not bad, "verstoesse": bad, "zentral": store, "zaehler": dict(sorted(counts.items()))}


def cmd_verify(args) -> int:
    import identity_store
    plan, plan_sha = _load_plan(args.plan)
    with open(os.path.join(args.backup, "backup.json"), encoding="utf-8") as fh:
        backup = json.load(fh)
    if backup.get("plan_sha256") != plan_sha:
        raise Stop(f"backup {args.backup} belongs to another plan")
    store, source = identity_store.resolve_store(args.identity_store, os.environ)
    if source == "home" and not identity_store.same_store(store, os.path.join(plan["home"], STORE)):
        raise Stop(f"the central store {store} is not the home store of the plan - pass --identity-store")
    if args.json and (os.path.exists(args.json) or _inside_store(args.json)):
        log(f"verify: --json {args.json} exists or lies inside a store - choose a new file outside every store")
        return 2
    report = verify_migration(plan, args.backup, backup, store)
    if args.json:
        with open(args.json, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    z = report["zaehler"]
    print(f"verify {store}: {'OK' if report['ok'] else 'VERSTOSS'} - {z.get('bullet', 0)} Zeilen, "
          f"{z.get('kandidaten', 0)} Kandidaten, {z.get('changelog', 0)} Changelog-Eintraege aus den Backup-Bytes")
    for v in report["verstoesse"][:30]:
        print(f"  {v}")
    return 0 if report["ok"] else 1


# ------------------------------------------------------------------------------------------------- soul inventory (Teil C)

SOUL_MD = "identity/soul.md"
SOUL_CANDIDATES = "identity/soul-candidates.md"
TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "soul-template.txt")


def soul_template_lines() -> set:
    """Every line of the soul.md init template, current and earlier versions (scripts/soul-template.txt)."""
    with open(TEMPLATE_FILE, encoding="utf-8-sig") as fh:
        lines = [ln.rstrip("\r\n").strip() for ln in fh]
    return {ln for ln in lines if ln and not ln.startswith("#") and not ln.startswith("<!--")}


def parse_soul_candidates(text: str, origin: dict) -> list[dict]:
    """One item per '## ' section (title + body) and per bullet block before the first section; the init stub and
    blank lines are no candidates."""
    items, current = [], None
    for row in parse_profile(text, origin):
        cls = row["klasse"]
        if cls == "titel" or cls == "platzhalter":
            continue
        if cls == "sektion":
            current = {"origin": dict(row["origin"]), "titel": row["roh"][3:].strip(), "zeilen": []}
            items.append(current)
            continue
        if row["sektion"] is None and cls == "bullet":
            current = {"origin": dict(row["origin"]), "titel": row["roh"], "zeilen": [row["roh"]]}
            items.append(current)
            continue
        if current is not None:
            current["zeilen"].append(row["roh"])
    return [{"art": "kandidat", "origin": i["origin"], "titel": i["titel"],
             "text": "\n".join([i["titel"]] + [z for z in i["zeilen"] if z != i["titel"]])} for i in items]


def soul_own_blocks(text: str, origin: dict, template: set) -> list[dict]:
    """The own lines of a soul.md: bullet blocks (with their continuation lines) and preamble lines that are neither a
    template line nor a placeholder."""
    blocks, current = [], None
    for row in parse_profile(text, origin):
        cls = row["klasse"]
        if cls in ("titel", "sektion", "platzhalter") or row["roh"].strip() in template:
            current = None
            continue
        if cls == "fortsetzung" and current is not None:
            current["zeilen"].append(row["roh"])
            continue
        current = {"art": "soul_zeile", "origin": dict(row["origin"]), "sektion": row["sektion"], "zeilen": [row["roh"]]}
        blocks.append(current)
    for blk in blocks:
        blk["text"] = "\n".join(blk.pop("zeilen"))
    return blocks


def cmd_soul(args) -> int:
    if os.path.exists(args.out) or _inside_store(args.out):
        log(f"soul: {args.out} exists or lies inside a store - choose a new file outside every .agent-memory")
        return 2
    gaps: list = []
    stores = find_stores(args.home, gaps)
    if gaps:
        raise Stop("folders could not be searched: " + "; ".join(gaps[:5]))
    template = soul_template_lines()
    items, zeilen = [], 0
    for rel in stores:
        root = os.path.join(args.home, *rel.split("/"))
        for name in (SOUL_CANDIDATES, SOUL_MD):
            path = os.path.join(root, *name.split("/"))
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as fh:
                    text = fh.read().decode("utf-8")
            except (OSError, UnicodeDecodeError) as ex:
                raise Stop(f"{rel}/{name} is unreadable ({ex.__class__.__name__})")
            origin = {"projekt": project_of(rel), "datei": name}
            if name == SOUL_CANDIDATES:
                items += parse_soul_candidates(text, origin)
            else:
                found = soul_own_blocks(text, origin, template)
                zeilen += sum(len(i["text"].split("\n")) for i in found)
                items += found
    kand = [i for i in items if i["art"] == "kandidat"]
    own = [i for i in items if i["art"] == "soul_zeile"]
    zaehler = {"kandidaten": len(kand), "kandidaten_stores": len({i["origin"]["projekt"] for i in kand}),
               "soul_bloecke": len(own), "soul_zeilen": zeilen, "soul_stores": len({i["origin"]["projekt"] for i in own})}
    with open(args.out, "x", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"format": FORMAT, "home": os.path.abspath(args.home), "zaehler": zaehler, "elemente": items},
                            ensure_ascii=False, indent=1) + "\n")
    print(f"soul-Inventar {args.out}: {zaehler}")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="migrate_identity.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="read only: sources, target and counts into a new plan file")
    p.add_argument("--home", default=None, help="home folder to search (default: the user's home, resolved now)")
    p.add_argument("--out", required=True, help="new plan file (must not exist, not inside a store)")
    p = sub.add_parser("backup", help="byte copy of identity/ and the queue of every planned store")
    p.add_argument("--plan", required=True)
    p.add_argument("--out", required=True, help="new folder outside every store")
    p = sub.add_parser("apply", help="write the central profile; profile.md last (the switch)")
    p.add_argument("--plan", required=True)
    p.add_argument("--backup", required=True, help="the backup folder of this plan")
    p.add_argument("--identity-store", default=None, help="central store (default: env, then the home store)")
    p.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    p.add_argument("--lock-timeout", type=float, default=10.0, help="seconds to wait for identity/profile.lock")
    p = sub.add_parser("verify", help="E2-E4 from the backup bytes; exit 0 = complete, 1 = violation")
    p.add_argument("--plan", required=True)
    p.add_argument("--backup", required=True)
    p.add_argument("--identity-store", default=None)
    p.add_argument("--json", default=None, help="write the report as JSON")
    p = sub.add_parser("soul", help="read only: soul candidates and own soul.md lines of every store (Teil C)")
    p.add_argument("--home", default=None)
    p.add_argument("--out", required=True)
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if not e.code else 1
    if args.cmd in ("plan", "soul") and args.home is None:
        args.home = os.path.expanduser("~")
    try:
        return {"plan": cmd_plan, "backup": cmd_backup, "apply": cmd_apply, "verify": cmd_verify, "soul": cmd_soul}[args.cmd](args)
    except Stop as e:
        log(f"STOP: {e}")
        return 2
    except OSError as e:  # a busy lock (LockTimeout) or an I/O error: profile.md was not written last
        log(f"STOP: {e.__class__.__name__}: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
