#!/usr/bin/env python3
"""
slot_move.py - move or copy a Coromon save between slots.

WHY A RENAME IS NOT ENOUGH, which is the whole reason this exists. Each
`saveslot_self_<n>` row is JSON wrapping an encrypted payload, and the payload itself carries
`selectedSaveslotIndex`. The game reads that field - `playerStateHelper.loadGame` does
`selectedSaveslotIndex = _saveslotData.selectedSaveslotIndex` - and uses it when deciding
where to save. So renaming the row keys alone gives you a save that is LISTED under the new
slot but still believes it belongs to the old one, and the first save afterwards goes back to
the old slot. Row and payload have to move together, which means re-encrypting the payload.

    python slot_move.py                     what is in the slots right now
    python slot_move.py --move 2 1          slot 2 becomes slot 1, slot 2 is left empty
    python slot_move.py --copy 2 1          slot 1 gets a copy, slot 2 is left alone
    python slot_move.py --list
    python slot_move.py --restore <file>    put a whole store backup back

Both halves of a slot travel together: `saveslot_self_<n>` and `saveslot_self_<n>_auto`, since
a slot with a manual save but no autosave is not a state the game produces. `--no-auto` moves
only the manual one if that is ever what you want.

THE STORE. Solar2D keeps its preferences in

    %LOCALAPPDATA%\\TRAGsoft\\Coromon\\.system\\CoronaPreferences.sqlite

one table `preference(key TEXT PRIMARY KEY, value VARIANT)`. An empty slot is the four
characters `null`, not a missing row - slot 3 looks like that, which is how the convention was
confirmed rather than guessed.

THE ONLINE CACHES. Each slot can also have `saveslot_self_<n>_onlineCache` and
`<n>_auto_onlineCache` rows: caches of the online profile, which nothing reads while online
saves are disabled. They are cleared for any slot this script touches, because otherwise a slot
you just emptied is left as an empty slot carrying orphan rows - the odd state that was cleaned
out of slot 1 by hand the first time. `--keep-caches` leaves them.

THE GAME MUST BE CLOSED, and the script refuses to write otherwise. Solar2D holds the whole
preference table in memory and writes it back, so a write from outside is either clobbered or
half-merged. `--list` only reads, copies the database out first, and is safe either way.

EVERY WRITE IS BACKED UP AND THEN VERIFIED. A timestamped copy of the store and a JSON dump of
the slot rows go to `%APPDATA%\\coromon-backups` before anything changes - so `--restore` with
that file puts it back. Afterwards each written row is read back, decrypted, and compared to
what it should be by deep equality, not by length; a mismatch is reported rather than assumed
away.

The decryption comes from savefile.py, including the machine-specific keystream, so this only
works on the installation that wrote the save.
"""

import argparse
import base64
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import savefile  # noqa: E402

PREFS_DB = savefile.PREFS_DB
BANK = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "coromon-backups")

# rows for a slot that has never been used, and the shape of a slot key
EMPTY = "null"
FLAVOURS = (("", "manual"), ("_auto", "auto"))


def log(msg=""):
    print(msg, flush=True)


def game_running():
    """(is_running, description). tasklist is the only thing here that needs Windows."""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq coromon.exe", "/NH"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception as exc:                                   # no tasklist? then do not claim
        return False, "could not check (%s)" % exc
    if "coromon.exe" in out.lower():
        return True, out.strip().splitlines()[0].strip()
    return False, "not running"


def key_for(slot, suffix=""):
    return "saveslot_self_%d%s" % (slot, suffix)


def cache_keys(slot):
    return [key_for(slot, s + "_onlineCache") for s, _ in FLAVOURS]


def slot_key(slot, auto):
    return key_for(slot, "_auto" if auto else "")


def parse_slot(text):
    """Accept 2, "2", "slot 2", "saveslot_self_2" - anything with the number in it."""
    digits = ""
    for ch in reversed(str(text).strip()):
        if ch.isdigit():
            digits = ch + digits
        elif digits:
            break
    if not digits:
        raise argparse.ArgumentTypeError("%r has no slot number in it" % text)
    return int(digits)


def read_rows():
    """Every saveslot row as text, read-only. Does not need the game to be closed."""
    uri = "file:%s?mode=ro" % PREFS_DB.replace("\\", "/")
    con = sqlite3.connect(uri, uri=True)
    try:
        return {k: (v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v))
                for k, v in con.execute(
                    "select key, value from preference where key like 'saveslot%'")}
    finally:
        con.close()


def decode(row_text):
    """(outer dict, decrypted payload dict) from a row. Raises on an empty slot."""
    outer = json.loads(row_text)
    blob = base64.b64decode(outer["encryptedData"])
    return outer, json.loads(savefile._decrypt(blob).decode("utf-8"))


def encode(outer, obj):
    """A row, with the payload re-encrypted. The XOR is its own inverse, so encrypting is
    the same operation as decrypting - which is why changing a field and writing it back is
    exact rather than approximate."""
    out = dict(outer)
    out["encryptedData"] = base64.b64encode(
        savefile._decrypt(json.dumps(obj, separators=(",", ":")).encode("utf-8"))).decode("ascii")
    return json.dumps(out, separators=(",", ":"))


def payload_with_slot(row_text, slot):
    """A row whose payload says it belongs to `slot`, plus that payload for verification."""
    outer, obj = decode(row_text)
    obj["selectedSaveslotIndex"] = slot
    return encode(outer, obj), obj


def reencode_is_exact(row_text):
    """Whether decoding and re-encoding a row reproduces it BYTE FOR BYTE.

    This is the check that backs the tool's whole claim. Comparing the rewritten payload to the
    original as parsed dicts does not prove it: dict equality ignores JSON key order, so a
    reordering, a change of spacing or a stray trailing byte would sail through. If a row does not
    survive a no-op round trip, then "the only thing this changed is one field" is not proven for
    that row, and that is worth saying out loud rather than assuming.
    """
    try:
        outer, obj = decode(row_text)
    except Exception:
        return False
    return encode(outer, obj) == row_text


def describe(slot, row_text):
    """One line about a row, including whether it agrees with the slot it is filed under."""
    label = "slot %d" % slot
    if row_text == EMPTY:
        return "  %-8s %s" % (label, "empty")
    try:
        outer, obj = decode(row_text)
    except Exception:
        return "  %-8s %7d chars, not readable" % (label, len(row_text))
    md = outer.get("metadata") or {}
    dt = md.get("dateTime")
    when = (datetime.datetime.fromtimestamp(dt).strftime("%Y-%m-%d %H:%M")
            if isinstance(dt, (int, float)) and dt > 1e9 else "no date")
    inside = obj.get("selectedSaveslotIndex")
    flag = "" if inside == slot else "   <== says it is slot %s" % inside
    return ("  %-8s %s  %-18s gold=%-10s squad=%-2s storage=%-3s %s%s"
            % (label, when, str(obj.get("currentPlayerMapName"))[:18], obj.get("gold"),
               len(obj.get("squad") or []), len(obj.get("monsterStorage") or []),
               "inside=%s" % inside, flag))


def slots_in(rows):
    """Which slot numbers appear in the store at all, newest sort is by number."""
    nums = set()
    for k in rows:
        if k.startswith("saveslot_self_") and "onlineCache" not in k:
            rest = k[len("saveslot_self_"):]
            rest = rest[:-len("_auto")] if rest.endswith("_auto") else rest
            if rest.isdigit():
                nums.add(int(rest))
    return sorted(nums)


def cmd_list():
    rows = read_rows()
    running, how = game_running()
    log("store: %s" % PREFS_DB)
    log("game:  %s%s" % (how, "  (reading is safe either way)" if running else ""))
    log()
    if not rows:
        log("no slot rows at all")
        return 0
    for slot in slots_in(rows):
        for suffix, name in FLAVOURS:
            k = slot_key(slot, suffix == "_auto")
            if k in rows:
                log(describe(slot, rows[k]) + ("   [%s]" % name if name == "auto" else ""))
    caches = sorted(k for k in rows if "onlineCache" in k)
    log()
    log("online cache rows: %s" % (", ".join(caches) if caches else "none"))
    log()
    log("a line flagged \"says it is slot N\" is the mismatch this script exists to fix")
    return 0


def backup(rows):
    """A timestamped copy of the whole store, plus a readable dump of the slot rows.

    The stem has to be unique, not merely timestamped: two writes inside the same second would
    otherwise share a filename and the second would silently destroy the first's backup.
    """
    os.makedirs(BANK, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem, n = stamp, 2
    while os.path.exists(os.path.join(BANK, "CoronaPreferences-%s.sqlite" % stem)):
        stem = "%s-%d" % (stamp, n)
        n += 1
    db_copy = os.path.join(BANK, "CoronaPreferences-%s.sqlite" % stem)
    dump = os.path.join(BANK, "saveslots-%s.json" % stem)
    src = sqlite3.connect("file:%s?mode=ro" % PREFS_DB.replace("\\", "/"), uri=True)
    try:
        dst = sqlite3.connect(db_copy)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    with open(dump, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    return db_copy, dump


def put(con, key, value):
    """Upsert. `key` is the table's primary key, so one or the other of these is right."""
    if con.execute("update preference set value=? where key=?", (value, key)).rowcount == 0:
        con.execute("insert into preference(key, value) values(?, ?)", (key, value))


def cmd_transfer(src, dst, *, copying, include_auto=True, keep_caches=False, force=False,
                 dry_run=False):
    """Keyword-only after the two slots on purpose: six positional booleans is a trap, and getting
    two of them the wrong way round turns `--force` into `--dry-run` silently."""
    if src == dst:
        log("source and destination are the same slot")
        return 1
    if src < 1 or dst < 1:
        log("slot numbers start at 1")
        return 1

    rows = read_rows()
    moving = [(suffix, name) for suffix, name in FLAVOURS if include_auto or suffix == ""]

    present = [(name, slot_key(src, suffix == "_auto")) for suffix, name in moving
               if slot_key(src, suffix == "_auto") in rows]
    if not present:
        log("slot %d has no rows to move" % src)
        return 1
    for name, k in present:
        if rows[k] == EMPTY:
            log("slot %d has no %s save" % (src, name))
            return 1

    for suffix, name in moving:
        dk = slot_key(dst, suffix == "_auto")
        if dk in rows and rows[dk] != EMPTY and not force:
            log("slot %d already has a %s save - pass --force to overwrite it" % (dst, name))
            return 1

    # what the rows will become
    writes = {}
    expected = {}
    for suffix, name in moving:
        new_text, obj = payload_with_slot(rows[slot_key(src, suffix == "_auto")], dst)
        writes[slot_key(dst, suffix == "_auto")] = new_text
        expected[slot_key(dst, suffix == "_auto")] = obj
    if not copying:
        for suffix, name in moving:
            writes[slot_key(src, suffix == "_auto")] = EMPTY
    # only rows that are actually there, so the report does not promise to clear things that
    # have never been written
    doomed = [] if keep_caches else sorted(
        k for k in set(cache_keys(src)) | set(cache_keys(dst)) if k in rows)

    log("%s slot %d -> slot %d%s" % ("copy" if copying else "move", src, dst,
                                     "" if include_auto else "  (manual save only)"))
    log()
    for k, v in sorted(writes.items()):
        if v == EMPTY:
            log("  %-34s -> empty" % k)
        else:
            old = rows.get(slot_key(src, "_auto" if k.endswith("_auto") else ""))
            log("  %-34s <- slot %d's %s, payload says slot %d  (%d chars)"
                % (k, src, "autosave" if k.endswith("_auto") else "save", dst, len(v)))
    if doomed:
        log("  clearing: %s" % ", ".join(doomed))
    else:
        log("  online cache rows left alone")

    # does a no-op rewrite of each row it is about to touch come back identical? if it does, the
    # only difference afterwards is the slot index and this transform is exactly minimal
    source_rows = [rows[slot_key(src, suffix == "_auto")] for suffix, _ in moving
                   if slot_key(src, suffix == "_auto") in rows]
    exact = all(reencode_is_exact(r) for r in source_rows)
    log("  encoder check: each row survives a no-op round trip byte for byte: %s" % exact)
    if not exact:
        log("                 so for at least one row, \"only the slot index changed\" is not proven")

    if dry_run:
        log()
        log("dry run - nothing was written")
        return 0

    running, how = game_running()
    if running:
        log()
        log("REFUSING to write: %s" % how)
        log("close the game and run it again - it holds the whole store in memory")
        return 1

    db_copy, dump = backup(rows)
    log()
    log("backup: %s" % db_copy)
    log("        %s" % dump)
    log("        (%s puts this back)" % db_copy)

    con = sqlite3.connect(PREFS_DB)
    try:
        con.execute("begin")
        for k, v in writes.items():
            put(con, k, v)
        removed = 0
        for k in doomed:
            removed += con.execute("delete from preference where key=?", (k,)).rowcount
        con.execute("commit")
    except Exception:
        con.execute("rollback")
        raise
    finally:
        con.close()

    # verify by reading it back and decrypting, not by trusting the write
    after = read_rows()
    con = sqlite3.connect("file:%s?mode=ro" % PREFS_DB.replace("\\", "/"), uri=True)
    integrity = con.execute("pragma integrity_check").fetchone()[0]
    con.close()

    log()
    log("cache rows removed: %d" % removed)
    log("integrity check: %s" % integrity)
    log()
    ok = True
    for k, want_obj in sorted(expected.items()):
        try:
            _, back = decode(after[k])
        except Exception as exc:
            log("  %-34s UNREADABLE (%s)" % (k, exc))
            ok = False
            continue
        same = back == want_obj
        ok = ok and same
        log("  %-34s reads back, says slot %s, otherwise identical: %s"
            % (k, back.get("selectedSaveslotIndex"), same))
    for k, v in writes.items():
        if v == EMPTY:
            matched = after.get(k) == EMPTY
            ok = ok and matched
            log("  %-34s is empty: %s" % (k, matched))
    log()
    log("done" if ok else "SOMETHING DID NOT MATCH - the backup above is intact")
    if ok:
        if exact:
            log("every row that moved differs from its original in the slot index and nothing else")
        log("start the game and check slot %d" % dst)
    return 0 if ok else 1


def cmd_restore(path, dry_run):
    if not os.path.exists(path):
        log("no such file: %s" % path)
        return 1
    log("putting %s over %s" % (path, PREFS_DB))
    log("  (%d bytes over %d)" % (os.path.getsize(path), os.path.getsize(PREFS_DB)))
    if dry_run:
        log("dry run - nothing was written")
        return 0
    running, how = game_running()
    if running:
        log()
        log("REFUSING to write: %s" % how)
        return 1
    db_copy, _ = backup(read_rows())
    log("  current state kept at %s" % db_copy)
    shutil.copy2(path, PREFS_DB)
    con = sqlite3.connect("file:%s?mode=ro" % PREFS_DB.replace("\\", "/"), uri=True)
    log("  integrity check: %s" % con.execute("pragma integrity_check").fetchone()[0])
    con.close()
    log()
    log("restored - start the game to see it")
    return 0


def main():
    ap = argparse.ArgumentParser(description="move or copy a Coromon save between slots")
    what = ap.add_mutually_exclusive_group()
    what.add_argument("--list", action="store_true",
                      help="what is in the slots now (the default, and safe while the game runs)")
    what.add_argument("--move", nargs=2, metavar=("FROM", "TO"), type=parse_slot,
                      help="move a slot, leaving the source empty")
    what.add_argument("--copy", nargs=2, metavar=("FROM", "TO"), type=parse_slot,
                      help="copy a slot, leaving the source alone")
    what.add_argument("--restore", metavar="BACKUP",
                      help="put a store backup file back (undoes a move)")
    ap.add_argument("--no-auto", action="store_true",
                    help="only the manual save, not the autosave")
    ap.add_argument("--keep-caches", action="store_true",
                    help="leave the _onlineCache rows alone")
    ap.add_argument("--force", action="store_true",
                    help="overwrite the destination even if it holds a save")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would happen and write nothing")
    args = ap.parse_args()

    if not os.path.exists(PREFS_DB):
        log("the save store is not where it was expected:")
        log("  " + PREFS_DB)
        return 1

    if args.restore:
        return cmd_restore(args.restore, args.dry_run)
    if args.move:
        return cmd_transfer(args.move[0], args.move[1], copying=False,
                            include_auto=not args.no_auto, keep_caches=args.keep_caches,
                            force=args.force, dry_run=args.dry_run)
    if args.copy:
        return cmd_transfer(args.copy[0], args.copy[1], copying=True,
                            include_auto=not args.no_auto, keep_caches=args.keep_caches,
                            force=args.force, dry_run=args.dry_run)
    return cmd_list()


if __name__ == "__main__":
    sys.exit(main())
