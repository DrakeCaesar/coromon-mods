#!/usr/bin/env python3
"""
reload_r3.py - press the right stick in game and the reload runs, with no terminal in between.

Run it, leave it running, play. Press the stick in and the game tears the world down and loads the
slot back out of the store - the same thing `quick_reload.py --reload` does, just triggered from the
controller instead of the keyboard. Press it again to do it again, which is the whole point of the
Potentiflator loop: hand the Coromon over, look at the answer, press, hand it over again.

WHERE THE WORK HAPPENS. This script does not re-implement the reload. `abandoned/quick_reload.py`
holds the implementation - the pre-flight, the teardown, the input-level repair, the load - and it is
the version that was tested against the game, so it is imported and called rather than copied. It
lives in `abandoned/` because it was written off once and then brought back; that is the only reason
it is not at the top level.

    python reload_r3.py                  bind the right stick and wait for presses
    python reload_r3.py --key button9    bind something else (repeatable)
    python reload_r3.py --watch          print the name of every key/button the game reports,
                                          so a name can be confirmed rather than guessed
    python reload_r3.py --unbind         remove the binding from the running game

HOW THE PRESS IS DETECTED. One `Runtime:addEventListener('key', ...)` of our own. That is a normal
subscription - the game's own input layer is built on the same events and other tools already listen
to them (ingame/sprint.py does) - so it neither replaces nor blocks anything of the game's.

Two measured facts decide the handler's shape:

  * Every key event is delivered TWICE in this build: a press arrives as `down, down` and a release
    as `up, up`. A handler that counts each `down` therefore fires twice per press, so the handler
    tracks the key's own state and only counts the first one.
  * Controller buttons are named, not numbered: Solar2D reports `leftJoystickButton` for pressing the
    left stick in (measured in sprint.py, and NOT one of the `button1`..`button10` range). The right
    stick is `rightJoystickButton` by the same convention - which is the default here, but `--watch`
    is there so it can be confirmed against your controller rather than taken on trust.

THE BINDING OUTLIVES THIS SCRIPT, and that is deliberate but worth knowing. The listener is pure Lua
inside the game, with no call back into Frida, so it survives this process exiting - it simply stops
doing anything, because nothing is polling the counter it increments. Bind once, quit, re-run later
and the first thing the script does is replace the old listener. `--unbind` removes it, and closing
the game removes it regardless.
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "abandoned"))
from scroll_fix import _bridge, _eval, _lua_quote  # noqa: E402
import quick_reload  # noqa: E402

DEFAULT_KEYS = ["rightJoystickButton"]
POLL = 0.15
LOST_AFTER = 5          # consecutive unreadable polls before treating the game as gone
WATCH_SECONDS = 30.0

# What a live game answers, and the chunk that asks. See ping().
PING = "return 'qr-ping'"
PING_ANSWER = "qr-ping"

# A game that is technically answering is not necessarily a game that is safe to install into. This
# asks whether the game's own bootstrap has finished, and it matters most on the RE-ATTACH path: the
# script is meant to survive the game being closed and started again, and a restart is exactly when an
# install can land mid-boot. Requiring modules then caches them in package.loaded half-built, and the
# game's own require of them later gets that instead of a fresh one - which shows up as the game
# hanging on a black screen at launch, sometimes, because it is a race. Same gate as ingame/core.py.
READY = r'''
if type(_G.require) ~= 'function' or type(_G.package) ~= 'table' then return 'boot' end
if type(_G.display) ~= 'table' then return 'boot' end
if type(_G.playerStateHelper) ~= 'table' then return 'boot' end
if type(_G.SaveslotPreferences) ~= 'table' then return 'boot' end
if type(_G.inputHelper) ~= 'table' then return 'boot' end
return 'ready'
'''


def log(msg):
    print(msg, flush=True)


def _key_set(keys):
    """The names as a Lua lookup table, lower-cased.

    Lower-cased here rather than at the comparison: Solar2D reports `buttonA`, so a table of the
    names as written would never match a lower-cased event name.
    """
    return "{" + ", ".join("[%s] = true" % _lua_quote(k.lower()) for k in keys) + "}"


BIND = r'''
-- Replace any listener we installed before, so re-running this script cannot stack them up.
if type(_G.__qrR3Off) == 'function' then
  pcall(_G.__qrR3Off)
  _G.__qrR3Off = nil
end
if type(Runtime) ~= 'table' or type(Runtime.addEventListener) ~= 'function' then
  return 'there is no Runtime to listen on'
end
do
  local WANT = __WANT__
  local down = false
  -- A fresh binding starts from zero: presses counted by an earlier run are not ours to act on.
  _G.__qrR3 = 0

  local function onKey(e)
    if type(e) ~= 'table' then return end
    local name = tostring(e.keyName or ''):lower()
    if not WANT[name] then return end
    -- up, up -> down; down, down -> count once (see the module docstring: every event is doubled)
    if e.phase == 'up' then
      down = false
      return
    end
    if e.phase ~= 'down' then return end
    if down then return end
    down = true
    _G.__qrR3 = (_G.__qrR3 or 0) + 1
  end

  Runtime:addEventListener('key', onKey)
  _G.__qrR3Off = function()
    Runtime:removeEventListener('key', onKey)
  end
end
return 'bound'
'''

UNBIND = r'''
if type(_G.__qrR3Off) == 'function' then
  pcall(_G.__qrR3Off)
  _G.__qrR3Off = nil
  return 'unbound - the stick does nothing now'
end
return 'there was no binding to remove'
'''

READ = "return tostring(_G.__qrR3 or 0)"

# Is the overlays.py install in this process? Its features wrap functions on the world and hold
# listeners into it (items, squad, potential, the gold getter, the dialogue press, the run-mode
# wrapper), and a reload TEARS THAT WORLD DOWN and builds a new one underneath them. Running the two
# together is not something either tool was built for: the worst case seen is a press landing on the
# world interface's interact button while its style container was gone, which crashes the game.
OVERLAYS = r'''
if type(_G.__hud) ~= 'table' or type(_G.__hud.feats) ~= 'table' then
  return 'not installed'
end
local names = {}
for k, f in pairs(_G.__hud.feats) do
  if type(f) == 'table' and f.on then names[#names + 1] = k end
end
table.sort(names)
if #names == 0 then return 'installed, nothing enabled' end
return 'installed: ' .. table.concat(names, ', ')
'''

WATCH = r'''
if type(_G.__qrWatchOff) == 'function' then
  pcall(_G.__qrWatchOff)
  _G.__qrWatchOff = nil
end
if type(Runtime) ~= 'table' or type(Runtime.addEventListener) ~= 'function' then
  return 'there is no Runtime to listen on'
end
do
  local seen, order = {}, {}
  _G.__qrKeys = ''
  local function onKey(e)
    if type(e) ~= 'table' then return end
    local k = tostring(e.keyName or '') .. ' / ' .. tostring(e.phase)
    if not seen[k] then
      seen[k] = true
      order[#order + 1] = k
      _G.__qrKeys = table.concat(order, ', ')
    end
  end
  Runtime:addEventListener('key', onKey)
  _G.__qrWatchOff = function()
    Runtime:removeEventListener('key', onKey)
  end
end
return 'watching'
'''

WATCH_OFF = r'''
if type(_G.__qrWatchOff) == 'function' then
  pcall(_G.__qrWatchOff)
  _G.__qrWatchOff = nil
  return 'stopped watching'
end
return 'was not watching'
'''


def game_ready(bridge):
    try:
        return bool(bridge.status().get("state"))
    except Exception:
        return False


def ping(bridge):
    """True only if the game answers a trivial chunk.

    `bridge.status()` is NOT enough, and this was found the hard way: once the game has gone, a
    Bridge can still report a state, and the next eval then comes back as a Frida-level error
    instead of a result - observed as "Error: access violation accessing 0xc". That looked like a
    chunk that had failed to parse, in the same program that had just printed "back - reinstalling
    the binding" for a game that was not there.
    """
    try:
        out = _eval(bridge, PING)
    except Exception:
        return False
    return out is not None and out.strip() == PING_ANSWER


# Markers of a Frida-level failure rather than a result from the game. A chunk's own error would say
# what went wrong inside Lua; these are the transport giving up.
GONE_MARKERS = (
    "access violation",
    "process not found",
    "unable to",
    "failed to",
    "not attached",
    "connection closed",
    "timed out",
    "script has been unloaded",
    "script destroyed",
)


def looks_gone(out):
    text = (out or "").lower()
    return any(m in text for m in GONE_MARKERS)


def attach(quiet=False):
    """A bridge to a running, answering game, or None.

    `_bridge()` itself waits up to 30 s for the process to appear, so this is the expensive call and
    is only made when there is reason to.
    """
    try:
        b = _bridge()
    except Exception:
        return None
    if not game_ready(b) or not ping(b):
        return None
    try:
        state = (_eval(b, READY) or "").strip()
    except Exception:
        return None
    if state != "ready":
        if not quiet:
            log("  the game is running but still starting up (%s) - not installing into it yet"
                % (state or "no answer"))
        return None
    if not quiet:
        log("  attached")
    return b


def wait_for_game(quiet=False):
    """Blocks until the game is up and answering. Used on the way in and after it goes away."""
    while True:
        b = attach(quiet=quiet)
        if b is not None:
            return b
        log("waiting for the game - start it and this will pick it up")
        time.sleep(2.0)


def parses(bridge, chunk, name):
    """loadstring the chunk inside the game before sending it for real.

    A chunk that does not parse would otherwise be discovered the hard way: the failure is silent at
    the point of sending, and an error inside a listener that has already been installed into the
    game freezes this process rather than reporting anything. Nothing is executed by the check.

    Returns True (parses), False (a real parse error), or None (the answer did not come from the
    game at all - it went away mid-call, and the caller should go back to waiting rather than
    report a parse failure that never happened).
    """
    code = (
        "local f, e = loadstring(%s, %s)\n"
        "return f and 'ok' or (' ' .. tostring(e))"
        % (_lua_quote(chunk), _lua_quote(name))
    )
    out = _eval(bridge, code)
    if out is not None and out.strip() == "ok":
        return True
    if looks_gone(out):
        return None
    log("  the chunk does not parse in the game's Lua: %s" % (out or "").strip())
    return False


def read_counter(bridge):
    """The press counter, or None when the game cannot be read (gone, restarting, or not answering).

    None is also what the bridge returns for a legitimately empty result, which is why the chunk
    answers a digits-only string and anything else counts as unreadable.
    """
    try:
        out = _eval(bridge, READ)
    except Exception:
        return None
    if out is None:
        return None
    out = out.strip()
    return int(out) if out.isdigit() else None


def install(bridge, keys):
    """True bound, False a real failure, None the game went away during the install."""
    ok = parses(bridge, BIND, "bind")
    if ok is None:
        return None
    if not ok:
        return False
    out = _eval(bridge, BIND.replace("__WANT__", _key_set(keys)))
    if out is None or "bound" not in out:
        if looks_gone(out):
            return None
        log("  the game did not accept the binding: %s" % (out or "no answer"))
        return False
    log("  bound: %s" % ", ".join(keys))
    return True


def warn_if_overlays(bridge):
    """Say so, once, if overlays.py is attached. See OVERLAYS for why that is a problem."""
    try:
        out = _eval(bridge, OVERLAYS)
    except Exception:
        return
    if not out or out.strip() == "not installed":
        return
    log("")
    log("!! overlays.py is attached and this reload will destroy the world under it:")
    log("   %s" % out.strip())
    log("   Its wrappers keep pointing at the world that is about to go away. Run one of")
    log("   the two at a time - stop overlays.py (Ctrl-C) and re-run it after the reloads.")
    log("")


def do_bind(keys):
    while True:
        bridge = wait_for_game()
        r = install(bridge, keys)
        if r is None:
            log("  the game went away before the binding was in - waiting")
            continue
        if not r:
            return 1
        break

    log("")
    log("press it in game. Leave this running; Ctrl-C here to stop.")
    log("the binding stays in the game until it is closed (or --unbind).")
    warn_if_overlays(bridge)

    last = read_counter(bridge)
    if last is None:
        last = 0
    lost = 0
    presses = 0
    while True:
        time.sleep(POLL)
        cur = read_counter(bridge)
        if cur is None:
            lost += 1
            if lost < LOST_AFTER:
                continue
            lost = 0
            log("")
            log("lost the game - waiting for it to come back")
            while True:
                bridge = wait_for_game(quiet=True)
                log("  back - reinstalling the binding")
                r = install(bridge, keys)
                if r is not None:
                    break
                log("  it went away again while installing - still waiting")
            if not r:
                return 1
            last = read_counter(bridge)
            if last is None:
                last = 0
            continue
        lost = 0
        if cur == last:
            continue
        presses += 1
        last = cur
        log("")
        log("=== %s pressed (%d) - reloading ===" % (", ".join(keys), presses))
        quick_reload.do_reload(None, bridge=bridge)
        log("=== reload finished - press it again to repeat ===")
        # Anything pressed WHILE that ran is dropped rather than queued: a reload takes about three
        # seconds, and firing a second one straight away is never what was meant.
        after = read_counter(bridge)
        if after is not None:
            last = after
    return 0


def do_watch():
    bridge = wait_for_game()
    if not parses(bridge, WATCH, "watch"):
        return 1
    _eval(bridge, WATCH)
    log("")
    log("press the button(s) you are interested in - %.0f seconds" % WATCH_SECONDS)
    log("")
    deadline = time.time() + WATCH_SECONDS
    seen = ""
    while time.time() < deadline:
        time.sleep(0.4)
        try:
            out = _eval(bridge, "return tostring(_G.__qrKeys or '')")
        except Exception:
            out = None
        if out and out != seen:
            seen = out
            log("  so far: " + seen)
    _eval(bridge, WATCH_OFF)
    log("")
    if seen:
        log("names seen: " + seen)
        log("use the part before the '/' with --key, e.g. --key rightJoystickButton")
    else:
        log("no key events arrived - is the game window focused?")
    return 0


def do_unbind():
    bridge = attach()
    if bridge is None:
        log("the game is not running, so there is no binding to remove")
        return 1
    out = _eval(bridge, UNBIND)
    log(out if out is not None else "the game did not answer")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", action="append", metavar="NAME",
                    help="button or key to bind (default: %s); repeatable" % DEFAULT_KEYS[0])
    ap.add_argument("--watch", action="store_true",
                    help="print the name of every key event for %.0f s, to confirm a name"
                         % WATCH_SECONDS)
    ap.add_argument("--unbind", action="store_true",
                    help="remove the binding from the running game")
    a = ap.parse_args()
    if a.watch:
        return do_watch()
    if a.unbind:
        return do_unbind()
    keys = [k.strip() for k in (a.key or DEFAULT_KEYS) if k.strip()]
    return do_bind(keys or DEFAULT_KEYS)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("")
        sys.exit(0)
