#!/usr/bin/env python3
"""autoroll.py - keep the Potentiflator rolling until it lands on a perfect.

Two of the three pieces already exist, so this only joins them up:

  * THE DECIDED RESULT, from steptimer's own queries. `decidedPotential()` reads
    `mon.didRerollPotential` and `mon.potential`, and the game decides the moment the Coromon is
    handed over - before the 1000 steps are walked. That is what makes a reroll loop cheap: the
    answer is readable without going anywhere near the end of the walk.
  * THE RELOAD, as `_G.__hud.feats.reload.fire()`. The feature exposes it (reload.py `f.fire =
    fire`) and guards it on `f.busy` itself, so calling it is cleaner than synthesising the
    gamepad button it is normally bound to.
  * THE CONFIRM PRESS, which is the one thing that has to come from outside: Space, as a real
    keyboard event, the way pad_drive.py drives a direction. There is deliberately no in-game
    synthetic input - `press_key_code()` in core.py is a direct call into the zoom's own handler
    and its docstring says the game's input system never sees it.

WHAT THIS DOES AND WHAT IT DOES NOT. The loop - noticing the decided result and firing the reload -
lives in the game, as the `autoroll` feature (ingame/autoroll.py). This script presses Space, which
is the one part that cannot come from inside, and reports what the feature is doing. It does not
decide anything and it does not reload anything, so there is no way for it to fire a reload twice:
the feature is installed once and the host tears the previous copy down before installing it again.

It also means one eval of a few lines per poll instead of a 12 KB chunk recompiled each time, and
it works whether or not this script is the one that turned the feature on.

WHAT IT NEEDS. overlays.py attached, with `[reload] enabled` and `[autoroll] enabled`. The game
window has to stay in the foreground: `keybd_event` is delivered to whatever owns the foreground,
so pressing with the game in the background would type a space into something else. That check
runs before every press and it pauses rather than doing it.

A SECOND SESSION. This attaches its own Bridge, exactly as perf_probe.py does, so while it runs
there are two Interceptors on `lua_gettop` and the frame cost roughly doubles. It only evals.

Usage:
    python autoroll.py

The numbers are the constants below - there are no command line options, by design, so there is
exactly one place to look to find out how fast this presses.
"""

import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from coromon_lua import MINIMAL_HOOKS, Bridge  # noqa: E402

import pad_drive  # noqa: E402  (reused for its Win32 helpers, not its CLI)

# ===========================================================================================
# THE NUMBERS. All of them, here, so nothing about the timing is hidden further down.
# ===========================================================================================
# The process to drive, and the game window's owner.
PROCESS = "coromon.exe"
# Seconds between Space presses. This is the ONLY thing that paces the presses - the loop wakes
# more often than this (POLL_INTERVAL) purely so a decided result is noticed quickly, without
# pressing any faster. 0.05 is what the press interval was tuned to before it became a constant
# here; it is fast enough that the dialogue cannot keep up, which is the point.
PRESS_INTERVAL = 0.05
# How often the game is asked what the autoroll feature is doing. Kept well under PRESS_INTERVAL,
# because this is also the delay between the game deciding a potential and the reload being fired -
# and every press in that window is a press past the handover.
POLL_INTERVAL = 0.12
# How long to sit still after the game window loses the foreground, before saying so again.
PAUSE_REPORT = 3.0

VK_SPACE = 0x20
SCAN_SPACE = 0x39


STATE_CODE = r"""
local f = _G.__hud and _G.__hud.feats and _G.__hud.feats.autoroll
if type(f) ~= 'table' then return '!the autoroll feature is not installed!' end
if not f.on then return '!the autoroll feature is switched off!' end
-- `|` between the fields, NOT a space. `last` is written as "P18 to P19", and splitting on
-- whitespace cut it at the first space - so the console showed the value the Coromon STARTED at
-- and never the one it was rolled to, which is the entire number this tool exists to show.
return string.format('rolls=%d|state=%s|last=%s|hit=%s|walk=%s|left=%s|blocked=%s',
  f.rolls or 0, tostring(f.state), tostring(f.last), tostring(f.hit),
  tostring(f.walk), tostring(f.left), tostring(f.blocked))
"""


def read_state(b):
    """The feature's own counters, as a dict, or an error string prefixed with '!'."""
    out = (b.eval(STATE_CODE, timeout=15.0) or {}).get("out") or ""
    if out.startswith("!"):
        return out
    if out == "":
        return "!no answer from the game!"
    fields = {}
    for part in out.split("|"):
        key, _, value = part.partition("=")
        if key:
            fields[key] = value
    return fields


def game_foreground(hwnd):
    """True when the game already owns the foreground.

    Checked before every press rather than calling focus(): keybd_event is delivered to whatever
    is in the foreground, so pressing while the game is in the background would type a space into
    something else - and focus() would drag the game back over whatever the user was doing.
    """
    return pad_drive.user32.GetForegroundWindow() == hwnd


def press_space():
    """Space, as a real keyboard event.

    NOT pad_drive.key(): that helper always sets KEYEVENTF_EXTENDEDKEY, which is correct for the
    arrow keys and wrong for Space - the scan code would be interpreted as an extended key and
    something else would arrive.
    """
    pad_drive.user32.keybd_event(VK_SPACE, SCAN_SPACE, 0, 0)
    time.sleep(0.05)
    pad_drive.user32.keybd_event(VK_SPACE, SCAN_SPACE, pad_drive.KEYEVENTF_KEYUP, 0)


def find_window():
    pid = pad_drive.process_pid()
    if pid is None:
        return None
    wins = pad_drive.windows_for_pid(pid)
    if not wins:
        return None
    # the visible, titled window is the game; fall back to the first one
    return next((h for h, t, v in wins if v and t), wins[0][0])


def report(st, presses):
    """ONE line, always the same shape, whatever happened.

    There used to be two alternating messages - a press counter and a roll line - and between them
    they said nothing the other did not. Everything worth knowing is here: how many rolls the
    feature has fired, how many times Space has been pressed, what the loop is doing, what the last
    decided result was, which direction it is holding, and how many steps the walk still owes.
    """
    print("rolls=%s presses=%d state=%s last=%s walk=%s left=%s blocked=%s" % (
        st.get("rolls"), presses, st.get("state"), st.get("last"),
        st.get("walk"), st.get("left"), st.get("blocked")))


def hold(key, held):
    """Hold `key` and let go of anything else. Returns what is held now.

    The walk is CONTINUOUS, so a direction has to be held rather than tapped - the game polls
    movement from held keys - which is why this is a different mechanism from the Space presses and
    why the driver has to track what it is holding.
    """
    if key == held:
        return held
    if held:
        pad_drive.key(held, False)
    if key:
        pad_drive.key(key, True)
    return key


def pump(b, hwnd):
    """Press Space while the feature waits for a handover, and report every change.

    The press is gated on PRESS_INTERVAL and the poll runs at POLL_INTERVAL, so the game is asked
    about four times as often as it is poked. That gap is the reason for two numbers rather than
    one: the delay between the game deciding a potential and the feature firing the reload is
    POLL_INTERVAL, and every press inside that window is a press past the handover.
    """
    seen = None
    presses = 0
    next_press = 0.0
    paused_at = 0.0
    held = None

    while True:
        st = read_state(b)
        if isinstance(st, str):
            print(st.strip("!"))
            hold(None, held)
            return 2

        state = st.get("state")
        line = (st.get("rolls"), state, st.get("last"), st.get("hit"), st.get("walk"),
                st.get("left"), st.get("blocked"))
        if line != seen:
            seen = line
            report(st, presses)

        if state in ("ready", "stuck"):
            hold(None, held)          # never leave a direction held down behind us
            return 0 if state == "ready" else 2

        # WHAT THE KEYS SHOULD BE DOING. Only one of the two things can be happening at a time:
        # while the loop waits for a handover it wants Space tapped, and while it walks it wants a
        # direction HELD. `walk` is what the feature asks for and it is nil when it wants nothing,
        # which is also how a finished walk stops the game moving.
        want = st.get("walk") if state == "walking" else None
        if want not in ("left", "right"):
            want = None

        now = time.monotonic()
        pressing = want is None and state == "waiting" and now >= next_press
        if (want != held or pressing) and not game_foreground(hwnd):
            if now - paused_at >= PAUSE_REPORT:
                paused_at = now
                print("the game lost the foreground - taking it back.")
            # keybd_event is delivered to whatever owns the foreground, so a press OR a held
            # direction needs the game to own it. focus() only un-minimises when the window is
            # actually minimised: SW_RESTORE also shrinks a MAXIMISED window, and Coromon's
            # fullscreen is exactly that, so this cannot drop the game out of fullscreen.
            pad_drive.focus(hwnd)
            time.sleep(POLL_INTERVAL)
            continue

        held = hold(want, held)
        if state == "waiting" and now >= next_press:
            press_space()
            presses += 1
            next_press = time.monotonic() + PRESS_INTERVAL
        time.sleep(POLL_INTERVAL)


def main():
    hwnd = find_window()
    if hwnd is None:
        print("%s is not running (or has no window)" % PROCESS)
        return 2

    b = Bridge(PROCESS, hooks=MINIMAL_HOOKS)
    try:
        for _ in range(150):
            if b.status().get("state"):
                break
            time.sleep(0.2)
        if not b.status().get("state"):
            print("attached, but no lua_State captured yet - is the game past its loading screen?")
            return 2

        # Take the foreground before anything is pressed. Later presses only need the game to still
        # own it, so the forced grab happens here, once, and the loop only takes it back if it is
        # lost. Nothing is pressed until the loop has looked at least once (it starts in
        # 'starting'), so this happens before the first Space rather than after it.
        took = pad_drive.focus(hwnd)
        print("window 0x%X: focus %s" % (
            hwnd, "taken" if took else "REFUSED - presses will wait until the game is clicked"))
        print("pressing Space every %gs - Ctrl+C to stop. The roll loop itself runs in the game, "
              "as the autoroll feature; this only reports it." % PRESS_INTERVAL)
        return pump(b, hwnd)
    finally:
        # A DIRECTION MAY STILL BE HELD DOWN if we are leaving in a hurry - Ctrl+C, or an error
        # raised while the walk was running. keybd_event has no cleanup of its own and there is no
        # "release everything", so the arrow would stay down and the player would keep walking
        # until they tapped the key themselves. Releasing both is harmless when neither is held.
        for direction in ("left", "right"):
            try:
                pad_drive.key(direction, False)
            except Exception:  # noqa: BLE001 - never let cleanup mask the real exit
                pass
        b.detach()


if __name__ == "__main__":
    sys.exit(main())
