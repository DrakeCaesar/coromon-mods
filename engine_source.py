#!/usr/bin/env python3
"""
engine_source.py - fetch the Solar2D engine source at the version this game was built from.

WHY THIS IS HERE
----------------
Coromon is a Solar2D game, and `CoronaLabs.Corona.Native.dll` **is** the engine - the exe is a
182 KB shell that loads it. Reading that source answers most questions about the game far
faster than disassembling the DLL: it is where the frame rate comes from
(`Runtime::BeginRunLoop` = `fTimer->SetInterval(1000 / fFPS)`), where the Win32 `WinTimer`
lives (`SetTimer(..., 10, ...)` plus a `GetTickCount()` deadline), and where `config.lua`'s
`application.content.fps` is read.

Two warnings, both learned the hard way:

  * **the version matters.** Tags are plain build numbers and the newest is not the one the
    game ships. The wrong tag is close enough to mislead - `3729` reads the fps field exactly
    where the shipped DLL does. This tool takes the version out of the game's own build
    metadata and clones *that* tag, rather than hard-coding one that rots at the next update.
  * **the public source is not the source the shipped DLL was built from.** Coromon's build
    accepts 60 and 120 fps - `cmp eax,0x3c / je / cmp eax,0x78 / jne / mov [esi+0x64], al` in
    `Runtime::ReadConfig` - while every public tag of `coronalabs/corona` still has
    `if ( 60 == fps )`. Use the source for *how the engine works*; the binary is the truth
    about *what this build does*.

USAGE
-----
    python coromon-tools/engine_source.py                  # what is there, and which tag it should be
    python coromon-tools/engine_source.py --clone           # fetch it, shallow (~620 MB)
    python coromon-tools/engine_source.py --clone --full    # with history (much bigger)
    python coromon-tools/engine_source.py --update          # move an existing clone to the tag the game wants
    python coromon-tools/engine_source.py --tag 3730        # a different tag, e.g. to compare

It lands next to these tools (`<game>/corona`), so grepping the engine and the game is one
`cd` apart. Nothing else in the repo depends on it being there, and it never touches the game
files - this is a read-only reference copy.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GAME = os.path.dirname(HERE)

REPO = "https://github.com/coronalabs/corona"
DEFAULT_NAME = "corona"
BUILD_METADATA = "build-metadata.json"
RUNTIME_MANIFEST = "runtime-manifest.json"

# Files worth naming after a clone, so the report proves the tree is the engine and not an
# empty directory: the main loop, the Windows frame timer, and the Win32 host.
LANDMARKS = [
    os.path.join("librtt", "Rtt_Runtime.cpp"),
    os.path.join("platform", "windows", "Corona.Native.Library.Win32", "Rtt", "Rtt_WinTimer.cpp"),
    os.path.join("platform", "windows", "Corona.Native.Library.Win32", "Interop", "RuntimeEnvironment.cpp"),
]


# ===========================================================================
# reading the version out of the game
# ===========================================================================
def metadata_version(game):
    """(version, filename) as the game itself states it, or (None, None)."""
    for filename, key in ((BUILD_METADATA, "solar2DVersion"), (RUNTIME_MANIFEST, "engineVersion")):
        path = os.path.join(game, filename)
        try:
            with open(path, encoding="utf-8") as fh:
                value = json.load(fh).get(key)
        except (OSError, ValueError):
            continue
        if value:
            return str(value), filename
    return None, None


def git(*args, cwd=None):
    """(ok, text) - git's stderr is returned as the text, so callers can show it verbatim."""
    try:
        proc = subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "git is not on PATH"
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        return False, ((proc.stderr or out).strip() or "git exited %d" % proc.returncode)
    return True, out


def git_visible(args, cwd=None):
    """Same, but the child writes straight to the terminal - cloning is slow enough that
    hiding git's progress meter would just look like a hang."""
    try:
        return subprocess.run(["git"] + list(args), cwd=cwd).returncode
    except FileNotFoundError:
        print("git is not on PATH", file=sys.stderr)
        return 1


def remote_commit(repo, tag):
    """The commit `tag` points at on the remote, or None when it is not there (or offline)."""
    ok, out = git("ls-remote", "--tags", repo, "refs/tags/%s" % tag, "refs/tags/%s^{}" % tag)
    if not ok:
        return None
    commit = None
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref.endswith("^{}"):
            return sha
        if ref == "refs/tags/%s" % tag:
            commit = sha
    return commit


def resolve_tag(repo, version):
    """Which number in the version is a real tag: '2026.3729.e' -> '3729'.

    Newest-looking component first, and verified against the remote rather than assumed, so a
    change of version format shows up as "no tag found" instead of as a wrong checkout.
    """
    for candidate in reversed(re.findall(r"\d+", version)):
        if remote_commit(repo, candidate) is not None:
            return candidate
    return None


def dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def local_state(path):
    """None if there is no clone there, else what it is at."""
    if not os.path.isdir(path):
        return None
    ok, commit = git("rev-parse", "--short", "HEAD", cwd=path)
    if not ok:
        return {"error": commit}
    _, tag = git("describe", "--tags", "--exact-match", "HEAD", cwd=path)
    _, url = git("remote", "get-url", "origin", cwd=path)
    _, dirty = git("status", "--porcelain", cwd=path)
    return {
        "commit": commit,
        "tag": tag or None,
        "url": url or "?",
        "dirty": bool(dirty),
        "size": dir_size(path),
        "landmarks": [f for f in LANDMARKS if os.path.exists(os.path.join(path, f))],
    }


# ===========================================================================
# commands
# ===========================================================================
def do_clone(repo, tag, path, full):
    if os.path.isdir(path):
        print("%s already exists - nothing cloned." % path)
        return 1
    args = ["clone", "--branch", tag]
    if not full:
        args += ["--depth", "1"]
    args += [repo, path]
    print("cloning %s at tag %s into %s%s" % (repo, tag, path, "" if full else " (shallow)"))
    if repo == REPO:
        print("  the engine tree is ~620 MB shallow, and several times that with history.")
        print("  Ctrl+C now if you did not mean it.\n")
    if git_visible(args) != 0:
        print("\nclone failed - the tag or the network, most likely.", file=sys.stderr)
        return 1
    print()
    return 0


def do_update(tag, path):
    """Move an existing clone to `tag` (a shallow clone can still fetch another tag)."""
    print("updating %s to tag %s" % (path, tag))
    if git_visible(["fetch", "--depth", "1", "origin",
                    "refs/tags/%s:refs/tags/%s" % (tag, tag)], cwd=path) != 0:
        print("\nfetch failed.", file=sys.stderr)
        return 1
    # Fully qualified: a bare tag name is ambiguous once a branch of the same name exists,
    # and git then warns (and can pick the wrong thing).
    if git_visible(["checkout", "--detach", "refs/tags/%s" % tag], cwd=path) != 0:
        print("\ncheckout failed.", file=sys.stderr)
        return 1
    print()
    return 0


def report(game, repo, tag_wanted, path):
    print("game      %s" % game)
    version, source = metadata_version(game)
    if version is None:
        print("engine    no %s or %s here - pass --tag" % (BUILD_METADATA, RUNTIME_MANIFEST))
    else:
        print("engine    %s   (from %s)" % (version, source))
    print("repo      %s" % repo)

    if tag_wanted:
        commit = remote_commit(repo, tag_wanted)
        if commit is None:
            print("tag       %s - NOT FOUND on the remote (renamed tag, or offline)" % tag_wanted)
        else:
            print("tag       %s   (commit %s)" % (tag_wanted, commit[:12]))
    print("clone     %s" % path)

    state = local_state(path)
    if state is None:
        print("          not there yet - run with --clone")
        return
    if "error" in state:
        print("          exists but is not a usable git clone: %s" % state["error"])
        return
    print("          %s at %s, %s, on disk"
          % (state["tag"] or "no tag (a branch, or detached)",
             state["commit"],
             "%d MB" % (state["size"] / 1e6)))
    print("          origin %s%s" % (state["url"], "  [uncommitted changes]" if state["dirty"] else ""))
    missing = [f for f in LANDMARKS if f not in state["landmarks"]]
    if missing:
        print("          WARNING: missing %d of %d expected source files" % (len(missing), len(LANDMARKS)))
    else:
        print("          landmarks ok: %d/%d" % (len(LANDMARKS), len(LANDMARKS)))
    if tag_wanted and state["tag"] and state["tag"] != tag_wanted:
        print("          the game was built from tag %s, this clone is %s - --update moves it"
              % (tag_wanted, state["tag"]))


def main():
    ap = argparse.ArgumentParser(
        description="Clone the Solar2D engine source at the tag this game was built from.")
    ap.add_argument("--game-dir", default=DEFAULT_GAME,
                    help="the folder holding coromon.exe (default: the parent of this folder)")
    ap.add_argument("--dir", default=None,
                    help="where to put the clone (default: %s next to this folder)" % DEFAULT_NAME)
    ap.add_argument("--repo", default=REPO, help="engine repository (default: %s)" % REPO)
    ap.add_argument("--tag", default=None,
                    help="tag to use instead of the one the game's build metadata names")
    ap.add_argument("--clone", action="store_true", help="fetch it")
    ap.add_argument("--full", action="store_true",
                    help="clone with history instead of --depth 1 (much bigger)")
    ap.add_argument("--update", action="store_true",
                    help="move an existing clone to the tag the game wants")
    args = ap.parse_args()

    game = os.path.abspath(args.game_dir)
    path = os.path.abspath(args.dir) if args.dir else os.path.join(game, DEFAULT_NAME)

    version, _ = metadata_version(game)
    tag = args.tag or (resolve_tag(args.repo, version) if version else None)

    if tag is None and (args.clone or args.update):
        raise SystemExit(
            "no tag to work with: %s" % ("none of the numbers in %r is a tag on %s"
                                         % (version, args.repo) if version else
                                         "the game's build metadata was not found - pass --tag"))

    rc = 0
    if args.clone or args.update:
        if not tag:
            raise SystemExit("no tag to use - pass --tag")
        rc = do_update(tag, path) if args.update else do_clone(args.repo, tag, path, args.full)

    report(game, args.repo, tag, path)
    if not (args.clone or args.update):
        print()
        print("clone it with:  python %s/engine_source.py --clone" % os.path.basename(HERE))
    return rc


if __name__ == "__main__":
    sys.exit(main())
