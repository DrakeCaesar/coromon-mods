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

IT TAKES THE LOOP OVER ON THE WAY IN, AND HANDS IT BACK ON THE WAY OUT. `arm()` runs once at
startup, and the feature's loop does NOTHING AT ALL until it does - which is the fix for the reload
firing on people who never ran this script: the install was what used to arm it, so merely having
the overlay attached reloaded the save on every handover that decided anything but the target.
`arm()` also asks for a fresh start, because the loop stops for good at `ready` - that is what keeps
it off a Coromon it has already rolled to the target - so `ready` outlives the Coromon it describes,
and a driver started after that Coromon was collected would read `ready`, press nothing, and exit.
`disarm()` runs on the way out (in a `finally`, since Ctrl+C is the normal ending), so quitting this
script stops the in-game reloading rather than leaving it running with nothing left to press.

It also means one eval of a few lines per poll instead of a 12 KB chunk recompiled each time, and
that the feature can stay `enabled` in `overlays.toml` without doing anything until this script arms
it.

WHAT IT NEEDS. overlays.py attached, with `[reload] enabled` and `[autoroll] enabled`. The game
window has to be in the foreground: `keybd_event` is delivered to whatever owns the foreground, so
pressing with the game in the background would type a space into something else. This script never
takes the focus to arrange that - it waits for the game to have it, printing when it is waiting, so
nothing is pressed and no direction is held until the game owns it.

A SECOND SESSION. This attaches its own Bridge, exactly as perf_probe.py does, so while it runs
there are two Interceptors on `lua_gettop` and the frame cost roughly doubles. It only evals.

Usage:
    python autoroll.py [potential]

With NO argument the loop aims at the potential the FEATURE was installed with (`TARGET` in
`ingame/autoroll.py`, 21 by default). An argument aims at another one:

    python autoroll.py 20      aim for a potent Coromon instead of a perfect one

Either way the target is pushed into the game with `setTarget()` before the loop is armed, so
nothing is re-installed and nothing is written anywhere - and because every run states its target,
a run with no argument goes back to the installed one rather than inheriting the last run's. 1..21
is the game's whole potential range and anything else is refused, because a target the game can
never decide would reload for ever.

The PRESS timing is still the constants below - by design, so there is exactly one place to look to
find out how fast this presses.
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
# How often the game is asked what the autoroll feature is doing. This number IS the wasted time,
# and it is paid in two places:
#
#   * WHILE THE WALK RUNS it is stand-still time. The feature flips the direction on the frame the
#     Coromon reaches the end of the corridor, but the Coromon is still held into the wall until
#     this loop notices and swaps the keys - so every millisecond here is a millisecond of the
#     1000-step walk not happening, paid at both ends of every traverse.
#   * WHILE WAITING FOR A HANDOVER it is the delay between the game deciding a potential and the
#     reload being fired, and every press inside that window is a press PAST the handover.
#
# So it wants to be as small as the round trip allows, and MEASURED on this machine the round trip
# is 3-5 ms - and the cost sits in Frida's message hop, NOT in the Lua that runs, because the fat
# seven-field read and a bare direction read cost the same (3.18 ms vs 2.99 ms median, n=300). The
# interval is therefore latency and nothing else; 0.005 puts the loop period at about 10 ms, which
# is where the round trip itself puts the floor. This is NOT the press rate - that is PRESS_INTERVAL,
# and it is gated separately.
POLL_INTERVAL = 0.005
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
return string.format('rolls=%d|state=%s|last=%s|hit=%s|walk=%s|left=%s|blocked=%s|target=%s',
  f.rolls or 0, tostring(f.state), tostring(f.last), tostring(f.hit),
  tostring(f.walk), tostring(f.left), tostring(f.blocked), tostring(f.target))
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


ARM_CODE = r"""
local f = _G.__hud and _G.__hud.feats and _G.__hud.feats.autoroll
if type(f) ~= 'table' then return '!the autoroll feature is not installed!' end
if not f.on then return '!the autoroll feature is switched off!' end
-- An installed-but-older chunk would have no arm(), and the loop would then do nothing at all while
-- this script pressed Space at it - which reads exactly like the loop being broken. Say so instead
-- of leaving it to be guessed at.
if type(f.arm) ~= 'function' then
  return '!this copy of the feature cannot be armed - restart overlays.py to reinstall it!'
end
__AIM__
local was = tostring(f.state)
-- ARMING IS WHAT MAKES THE LOOP RUN AT ALL (see ingame/autoroll.py), and it resets the counters on
-- the way in: a driver arriving means a fresh run.
f.arm()
return was
"""

# THE AIMED POTENTIAL, pushed in before arming so the run's FIRST decision already uses it. Set here
# rather than by writing overlays.toml because the target is one number for one run: the toml edit
# would re-install every feature and persist the change, and the user asked for neither. An older
# copy of the feature has no setTarget - a refusal beats driving a loop aimed at the wrong number.
AIM_CODE = r"""if type(f.setTarget) ~= 'function' then
  return '!this copy of the feature cannot be aimed - restart overlays.py to reinstall it!'
end
local okT, said = f.setTarget(__TARGET__)
if not okT then return '!the target was refused by the feature: ' .. tostring(said) .. '!' end
"""

# THE OTHER HALF OF THE PAIR, and the one that matters when this script dies: the loop lives in the
# game process and outlives this one, so an armed loop with nothing left to press the confirm key is
# worse than an idle one - it would keep reloading a save nobody was rolling.
DISARM_CODE = r"""
local f = _G.__hud and _G.__hud.feats and _G.__hud.feats.autoroll
if type(f) ~= 'table' or type(f.disarm) ~= 'function' then return '!not armed by this feature!' end
f.disarm()
return 'ok'
"""


def arm(b, target=None):
    """Take the loop over: allow it to fire reloads, and clear its memory of the last Coromon.

    TWO THINGS IN ONE CALL, because both of them mean "this run starts here".

    * ARMED - the feature does nothing at all until this happens (`f.armed`, false at install). The
      reload only exists to serve a run somebody is driving, and the confirm press cannot come from
      inside the game, so an overlay that arms itself on install is reloading a save nobody is
      rolling. That was the bug this fixes; see the module docstring.
    * RESET - `ready` is TERMINAL in the feature, deliberately: it is how the loop stops once it has
      hit the target, which is what keeps a reload away from the perfect Coromon it has just walked.
      But terminal means the state OUTLIVES the Coromon it describes - collect that Coromon, hand in
      a new one, and the loop is still sitting at `ready` with the old result and a step count of
      zero. A restarted driver then does nothing whatsoever: its first read says `ready`, and it
      exits having pressed nothing.

    AND, WITH A `target`, A THIRD: the potential to aim for, pushed in BEFORE the arm so the run's
    first decision already uses it (see AIM_CODE). The caller always supplies one - main passes the
    installed default when no argument was given - so a leftover value from an earlier run cannot
    persist invisibly.

    WHY THE RESET IS HERE AND NOT IN THE FEATURE. The feature could do it the moment its terminal
    deposit goes away, and in the long run that is the better home for it. What it cannot fix is
    the RACE: the driver's first read may land before the loop's next tick has noticed anything, so
    it would still see the inherited `ready` and stop. Asking on the way in has no race, and the
    state machine keeps its single, meaningful meaning of terminal.

    The loop's own reset is what runs - `arm()` in the feature, the same code its install path runs
    - rather than this script assigning to fields itself, so the feature stays the only thing that
    knows what it remembers. A run already in progress survives being re-armed: the loop goes back
    to 'starting', reads the deposit again, and because a walk's remaining steps are the game's own
    absolute countdown, it carries on from where it was.

    Returns the state that was replaced, or an error string prefixed with '!' like read_state.
    """
    # THE SENTINEL IS ALWAYS REPLACED, and with a target ALWAYS SUPPLIED by the caller - including
    # when no argument was given, where main passes the installed default. Two reasons, and both are
    # about the value outliving the run: `f.target` lives on the feature table and survives the loop's
    # own reloads and this process exiting, so a run that set 20 and then a run that set nothing would
    # silently keep aiming at 20. Saying it every time makes the rule "each run aims at what was asked
    # or the installed default" true rather than almost-true. (And `__AIM__` left unreplaced would be
    # a BARE IDENTIFIER on its own line, which is not a statement in Lua: the whole eval would fail.)
    code = ARM_CODE.replace("__AIM__", AIM_CODE.replace("__TARGET__", str(int(target))))
    out = (b.eval(code, timeout=15.0) or {}).get("out") or ""
    if out == "":
        return "!no answer from the game!"
    return out


def disarm(b):
    """Hand the loop back: stop the reloading, so it cannot continue without a driver.

    Expected to fail harmlessly - the usual reason for leaving is that the game closed, and the
    `finally` that calls this must never turn that into an exception of its own.
    """
    try:
        b.eval(DISARM_CODE, timeout=15.0)
    except Exception as exc:                       # noqa: BLE001 - cleanup must not mask the exit
        print("could not disarm the roll loop on the way out: %s: %s"
              % (type(exc).__name__, exc))


def game_foreground(hwnd):
    """True when the game already owns the foreground.

    Checked before every press rather than calling focus(): keybd_event is delivered to whatever
    is in the foreground, so pressing while the game is in the background would type a space into
    something else. This script therefore WAITS for the game to own it rather than taking it - 
    moving the game in front of whatever the user is looking at is not something an unattended run
    should do, and the wait costs one click.
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
    print("rolls=%s presses=%d state=%s last=%s walk=%s left=%s blocked=%s aiming=P%s" % (
        st.get("rolls"), presses, st.get("state"), st.get("last"),
        st.get("walk"), st.get("left"), st.get("blocked"), st.get("target")))


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

    TWO CADENCES, DELIBERATELY SEPARATE. Pressing is paced by PRESS_INTERVAL and by nothing else -
    the loop simply wakes far more often than it presses. Polling is paced by POLL_INTERVAL, which
    is the loop's floor and is there to be small: the round trip only costs 3-5 ms, and everything
    this loop spends beyond that is a delay the Coromon or the roll pays for (see the constants).
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
            # WAIT FOR IT, DO NOT TAKE IT. `focus()` used to be called here and at startup, which
            # yanks the game in front of whatever the user was looking at - and leaving the machine
            # alone while it rolls is the whole point of automating this. keybd_event is delivered
            # to whatever owns the foreground, so the only safe thing to do is wait for the game to
            # have it: nothing is pressed, and no direction is asked for, until it does.
            if now - paused_at >= PAUSE_REPORT:
                paused_at = now
                print("waiting for the game to take the foreground back - click it.")
            time.sleep(POLL_INTERVAL)
            continue

        held = hold(want, held)
        if state == "waiting" and now >= next_press:
            press_space()
            presses += 1
            next_press = time.monotonic() + PRESS_INTERVAL
        time.sleep(POLL_INTERVAL)


def installed_target():
    """The potential the FEATURE was installed with - what a run with no argument aims at.

    Read from `ingame/autoroll.py` rather than repeated here, because that constant is what the
    installed chunk was built with and the two must not drift. A copy of the feature that cannot be
    imported is not a reason to refuse to run, hence the fallback.
    """
    try:
        from ingame import autoroll as feature
        return int(feature.TARGET)
    except Exception:  # noqa: BLE001 - a missing constant is not worth failing the run over
        return 21


def parse_target(argv):
    """The optional potential to aim for: `python autoroll.py 20`.

    Returns (target, complaint). No argument means None - "whatever the installed feature is set to" -
    which is what every run did before this existed, so the plain `python autoroll.py` is unchanged.
    The range check is not politeness: the feature refuses a target it could never decide, and 1..21
    is the game's whole potential range, so this is the same rule said one layer earlier where the
    message can name the argument.
    """
    if not argv:
        return None, None
    if len(argv) > 1:
        return None, "at most one argument (the potential to aim for), got %d" % len(argv)
    try:
        target = int(argv[0])
    except ValueError:
        return None, "%r is not a number" % (argv[0],)
    if not 1 <= target <= 21:
        return None, "%d is outside the potential range 1..21" % target
    return target, None


def main():
    target, complaint = parse_target(sys.argv[1:])
    if complaint:
        print("autoroll: %s" % complaint)
        print("usage: python autoroll.py [potential 1..21]")
        print("       with no argument, the potential the feature was installed with")
        return 2
    # ALWAYS A CONCRETE TARGET, never "nothing": see arm() for why a run must state it.
    aim = target if target is not None else installed_target()
    hwnd = find_window()
    if hwnd is None:
        print("%s is not running (or has no window)" % PROCESS)
        return 2

    b = Bridge(PROCESS, hooks=MINIMAL_HOOKS)
    # Whether the loop was actually TAKEN OVER, so the cleanup only hands back what it took: an
    # exit before arming (the game not past its loading screen, say) must not report a failure to
    # disarm something that was never armed.
    took_over = False
    try:
        for _ in range(150):
            if b.status().get("state"):
                break
            time.sleep(0.2)
        if not b.status().get("state"):
            print("attached, but no lua_State captured yet - is the game past its loading screen?")
            return 2

        # ARM IT BEFORE ANYTHING ELSE, and before waiting for the foreground for no better reason
        # than that this is the order the messages read in: the loop does nothing at all until this
        # lands (see ingame/autoroll.py), and it may also be sitting in a terminal state left over
        # from the last Coromon - see arm() for why that strands a restarted run.
        was = arm(b, aim)
        if was.startswith("!"):
            print(was.strip("!"))
            return 2
        took_over = True
        print("roll loop armed (was %s) - aiming for P%d%s" % (
            was, aim, " (the installed target)" if target is None else " (from the argument)"))

        # WAIT FOR THE FOREGROUND, DO NOT TAKE IT. Nothing may be pressed while the game is behind
        # something else, because keybd_event goes to whatever owns the foreground - and grabbing it
        # here would pull the game in front of whatever the user is doing, which is exactly what an
        # unattended run must not do. Giving the game the foreground is one click, so this waits for
        # it and says so once.
        if not game_foreground(hwnd):
            print("window 0x%X does not have the foreground - click the game and this starts. "
                  "Nothing is pressed until then." % hwnd)
            while not game_foreground(hwnd):
                if b.gone:
                    print("the game closed.")
                    return 2
                time.sleep(0.5)
        print("window 0x%X has the foreground." % hwnd)
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
        # AND HAND THE LOOP BACK, for the same reason the keys are released: it runs in the game
        # and outlives this process, so an armed loop left behind would keep reloading on every
        # handover with nothing left to press the confirm key.
        if took_over:
            disarm(b)
        b.detach()


if __name__ == "__main__":
    sys.exit(main())
