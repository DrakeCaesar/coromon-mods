#!/usr/bin/env python3
"""
fps_patch.py - set Coromon's frame rate in the game's own files, and put it back.

HOW THE GAME PICKS IT
---------------------
Solar2D reads the frame rate ONCE, at startup, from the game's `config.lua`:

    application.content.fps

and uses it for the whole session - `Runtime::BeginRunLoop()` is
`fTimer->SetInterval(1000 / fFPS)`. Two things follow from that, both measured:

  * `display.fps` from Lua is a dead knob. Setting it to 120/165/240 at runtime changes
    nothing at all (60.0 fps / 16.67 ms per frame, every time).
  * the file has to be edited and the game restarted. Nothing can do it live.

The engine the game ships (Solar2D 2026.3729) accepts exactly two values. Disassembled at
RVA 0x183595 of `CoronaLabs.Corona.Native.dll`, right before the `exitOnError` read in
`Runtime::ReadConfig`:

    cmp eax, 0x3c                       ; fps == 60 ?
    je  store
    cmp eax, 0x78                       ; fps == 120 ?
    jne skip
    store: mov byte ptr [esi+0x64], al  ; fFPS = fps

Anything else is IGNORED and fFPS keeps its default of 30 - so writing 165 into config.lua
without unlocking the engine makes the game run at HALF its normal speed. That is why
--set refuses a value the engine does not know unless the check has been patched out.

THE TWO PATCHES
---------------
    config  Resources/resource.car          the double next to the "fps" key in the
                                            compiled config.lua. Written whole (8 bytes),
                                            so any integer works.
    engine  CoronaLabs.Corona.Native.dll    the two bytes of that `jne`, replaced with
                                            nops so every value is stored. fFPS is a U8,
                                            so the useful range is 1-255.

Both are found by pattern, not by a stored offset - a Steam update moves them, and the
tool says so instead of writing somewhere wrong. --restore always writes back exactly what
the game ships (60 and the unpatched jump), so there is no state to lose.

WHAT IT DOES NOT FIX
--------------------
  * Presentation is vsync-locked, so the ceiling is the monitor's refresh rate. That also
    explains the odd numbers: 60 fps at 120 Hz, and 55 fps at 165 Hz (a 16.67 ms frame
    lands on the 3rd 6.06 ms vsync, i.e. 18.18 ms).
  * The Win32 frame timer polls with a 10 ms SetTimer, so what you ask for is not
    necessarily what you get. Measure it (`perf_probe.py`) rather than trust the number.
  * Game speed does not change: durations are absolute milliseconds (a tile crossing is
    280 ms at normal speed, the same at any frame rate). The one thing that does count
    frames is `tiledAnimationProxyBuilder`, which steps animated tiles by
    `display.msPerFrame`; if those look fast, pin the game's sense of time with
    `display.msPerFrame = 16.6667` (it is settable at runtime).
  * A patched file fails Steam's "verify integrity of game files", which puts the
    originals back - that undoes the patch, it does not break anything.

USAGE
-----
    python coromon-tools/fps_patch.py                              # report what the files say now
    python coromon-tools/fps_patch.py --set 120                    # the one supported step up
    python coromon-tools/fps_patch.py --unlock-engine --set 165    # any value, e.g. a 165 Hz panel
    python coromon-tools/fps_patch.py --restore                    # back to the shipped 60/locked

Close the game before touching the DLL: Windows will not let anything write to a module
that is loaded, and the tool will tell you so. The archive can be written while the game
runs, but the new value is only read at startup, so restart it either way.
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import car_extract as ce  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GAME = os.path.dirname(HERE)

REL_CAR = os.path.join("Resources", "resource.car")
REL_DLL = "CoronaLabs.Corona.Native.dll"

CONFIG_FILE = "config.lu"
CONFIG_KEY = b"fps\x00"  # the key in the compiled config.lua
CONFIG_TAG = 3  # Lua 5.1 constant tag for a number

STOCK_FPS = 60.0  # what the game ships
ENGINE_OK = (60, 120)  # what an unpatched engine will accept

# cmp eax,0x3c / je +5 / cmp eax,0x78 / jne +3 / mov byte ptr [esi+0x64],al
#   ^0          ^3     ^5             ^8    ^10
ENGINE_SIG = b"\x83\xf8\x3c\x74\x05\x83\xf8\x78\x75\x03\x88\x46\x64"
ENGINE_JUMP = 8  # where the `jne +3` sits inside the signature
NOP = b"\x90\x90"  # jne +3 -> fall through to the store

FPS_MIN, FPS_MAX = 1, 255  # fFPS is a single byte


# ===========================================================================
# config: the fps double inside the compiled config.lua in resource.car
# ===========================================================================
def car_fps_offset(car):
    """Absolute file offset of the fps double, or None if it is not where we expect."""
    entry = None
    for typ, off, name in ce.parse_toc(car):
        if name == CONFIG_FILE:
            entry = (typ, off)
            break
    if entry is None:
        return None
    typ, off = entry
    _, _, _, data = ce.read_entry(car, typ, off)
    i = data.find(CONFIG_KEY)
    if i < 0 or i + 13 > len(data) or data[i + 4] != CONFIG_TAG:
        return None
    # entry header (12) + payload offset of the key + "fps\0" + tag
    return off + 12 + i + 5


def read_car_fps(car):
    """(offset, value) or (None, None)."""
    off = car_fps_offset(car)
    if off is None:
        return None, None
    with open(car, "rb") as fh:
        fh.seek(off)
        return off, struct.unpack("<d", fh.read(8))[0]


def write_car_fps(car, value, off=None):
    if off is None:
        off, _ = read_car_fps(car)
    if off is None:
        raise SystemExit(
            "config.lua's fps key is not where it is expected in %s -\n"
            "the game has probably been updated, so this tool needs re-checking "
            "before it writes anything." % car
        )
    with open(car, "r+b") as fh:
        fh.seek(off)
        fh.write(struct.pack("<d", value))
    return off


# ===========================================================================
# engine: the whitelist check in the runtime DLL
# ===========================================================================
def read_engine(dll):
    """(file offset of the accept-120 jump, its 2 bytes) or (None, None)."""
    with open(dll, "rb") as fh:
        data = fh.read()
    for patched, replacement in ((False, None), (True, NOP)):
        sig = ENGINE_SIG
        if patched:
            sig = ENGINE_SIG[:ENGINE_JUMP] + NOP + ENGINE_SIG[ENGINE_JUMP + 2:]
        i = data.find(sig)
        if i >= 0:
            j = i + ENGINE_JUMP
            return j, data[j:j + 2]
    return None, None


def write_engine(dll, data_two_bytes):
    off, _ = read_engine(dll)
    if off is None:
        raise SystemExit(
            "the engine's frame rate check is not where it is expected in %s -\n"
            "the game has probably been updated, so this tool needs re-checking "
            "before it writes anything." % dll
        )
    try:
        with open(dll, "r+b") as fh:
            fh.seek(off)
            fh.write(data_two_bytes)
    except PermissionError:
        raise SystemExit(
            "%s is in use.\nClose Coromon and run this again - Windows will not let "
            "anything write to a loaded DLL." % dll
        ) from None
    return off


# ===========================================================================
# the report
# ===========================================================================
def report(game, stream=sys.stdout):
    car = os.path.join(game, REL_CAR)
    dll = os.path.join(game, REL_DLL)

    print("game      %s" % game)
    print("config    %s" % car)

    fps = None
    if not os.path.exists(car):
        print("          MISSING")
    else:
        off, fps = read_car_fps(car)
        if off is None:
            print("          could not find the fps key in config.lua")
        else:
            print("          application.content.fps = %s  (file offset 0x%x)%s"
                  % (_fmt(fps), off, "   [stock]" if fps == STOCK_FPS else ""))

    if not os.path.exists(dll):
        print("engine    %s  MISSING" % dll)
        engine_unlocked = None
    else:
        off, current = read_engine(dll)
        if off is None:
            print("engine    %s" % dll)
            print("          could not find the frame rate check")
            engine_unlocked = None
        else:
            engine_unlocked = current == NOP
            print("engine    %s  (check at offset 0x%x, %s)"
                  % (dll, off, current.hex()))
            print("          %s"
                  % ("UNLOCKED - any value 1-255 is accepted"
                     if engine_unlocked
                     else "accepts exactly %s" % " and ".join(str(v) for v in ENGINE_OK)))

    if fps is not None and engine_unlocked is not None:
        print()
        if engine_unlocked:
            if not float(fps).is_integer() or not FPS_MIN <= fps <= FPS_MAX:
                print("WARNING   the engine wants an integer 1-%d and will store the low "
                      "byte of anything else" % FPS_MAX)
            else:
                print("result    the game will ask for %s fps" % int(fps))
        elif fps in ENGINE_OK:
            print("result    the game will ask for %d fps" % int(fps))
        else:
            print("WARNING   the engine ignores %s and falls back to 30 fps - half speed."
                  % _fmt(fps))
            print("          run with --unlock-engine --set %s to allow it, or --restore."
                  % _fmt(fps))
    print()
    print("a new value is only read at startup: restart the game for it to take effect.")


def _fmt(value):
    return "%g" % value


# ===========================================================================
# commands
# ===========================================================================
def engine_is_unlocked(game):
    """True when the runtime's check is patched out (None if it cannot be found)."""
    off, current = read_engine(os.path.join(game, REL_DLL))
    if off is None:
        return None
    return current == NOP


def do_set(game, value, unlocked):
    if not float(value).is_integer():
        raise SystemExit("%s is not a whole number of frames per second" % _fmt(value))
    value = int(value)
    if not FPS_MIN <= value <= FPS_MAX:
        raise SystemExit("fps must be %d-%d (the engine keeps it in one byte)"
                         % (FPS_MIN, FPS_MAX))

    if value not in ENGINE_OK and not unlocked:
        raise SystemExit(
            "%d is not a frame rate this engine knows (it accepts %s).\n"
            "The check has to be patched out first - close the game and run:\n"
            "    %s --unlock-engine --set %d"
            % (value, " or ".join(str(v) for v in ENGINE_OK),
               os.path.basename(__file__), value)
        )

    car = os.path.join(game, REL_CAR)
    off, before = read_car_fps(car)
    if before == value:
        print("config  %s: already %d" % (REL_CAR, value))
        return
    write_car_fps(car, value, off)
    print("config  %s: fps = %s -> %d  (file offset 0x%x)"
          % (REL_CAR, _fmt(before), value, off))


def do_engine(game, unlock):
    dll = os.path.join(game, REL_DLL)
    off = write_engine(dll, NOP if unlock else ENGINE_SIG[ENGINE_JUMP:ENGINE_JUMP + 2])
    print("engine  %s: %s  (offset 0x%x)"
          % (REL_DLL, "unlocked" if unlock else "locked to %s" % ", ".join(map(str, ENGINE_OK)), off))


def do_restore(game):
    car = os.path.join(game, REL_CAR)
    off, fps = read_car_fps(car)
    if fps is None:
        raise SystemExit("nothing to restore in %s" % car)
    if fps != STOCK_FPS:
        write_car_fps(car, STOCK_FPS, off)
        print("config  %s: fps = %s -> %s  (file offset 0x%x)"
              % (REL_CAR, _fmt(fps), _fmt(STOCK_FPS), off))
    else:
        print("config  %s: already the shipped %s" % (REL_CAR, _fmt(STOCK_FPS)))

    dll = os.path.join(game, REL_DLL)
    off, current = read_engine(dll)
    stock = ENGINE_SIG[ENGINE_JUMP:ENGINE_JUMP + 2]
    if off is None:
        print("engine  %s: check not found, left alone" % REL_DLL)
    elif current == stock:
        print("engine  %s: already the shipped check" % REL_DLL)
    else:
        off = write_engine(dll, stock)
        print("engine  %s: check restored  (offset 0x%x)" % (REL_DLL, off))


def main():
    ap = argparse.ArgumentParser(
        description="Set Coromon's frame rate in the game's own files, and put it back.")
    ap.add_argument("--game-dir", default=DEFAULT_GAME,
                    help="the folder holding coromon.exe (default: the parent of this folder)")
    ap.add_argument("--set", dest="fps", type=float, default=None,
                    help="frame rate to write into config.lua (%d-%d; only %s unless the "
                         "engine is unlocked)" % (FPS_MIN, FPS_MAX,
                                                  " and ".join(map(str, ENGINE_OK))))
    ap.add_argument("--unlock-engine", action="store_true",
                    help="patch the runtime so it accepts any %d-%d (needs the game closed)"
                         % (FPS_MIN, FPS_MAX))
    ap.add_argument("--lock-engine", action="store_true",
                    help="put the runtime's check back")
    ap.add_argument("--restore", action="store_true",
                    help="put both files back to what the game ships")
    ap.add_argument("--report", action="store_true",
                    help="show what the files say now (this is the default)")
    args = ap.parse_args()

    game = os.path.abspath(args.game_dir)
    if not os.path.exists(os.path.join(game, REL_CAR)):
        raise SystemExit("no %s under %s - pass --game-dir" % (REL_CAR, game))

    # The engine first: if patching it fails (the game is still running) the config must
    # not be written, or the game would ask for a frame rate the engine ignores - which
    # silently means 30 fps, half speed.
    if args.unlock_engine or args.lock_engine:
        do_engine(game, args.unlock_engine)
    if args.restore:
        do_restore(game)
    if args.fps is not None:
        do_set(game, args.fps, unlocked=engine_is_unlocked(game))

    if args.fps is not None or args.restore or args.unlock_engine or args.lock_engine:
        print()
    report(game)
    return 0


if __name__ == "__main__":
    sys.exit(main())
