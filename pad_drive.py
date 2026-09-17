#!/usr/bin/env python3
"""
pad_drive.py - hold a direction in the running Coromon window, from outside.

Coromon is a Solar2D (SDL) game, so movement input arrives as ordinary window
messages. There is no XInput path we can synthesise without a ViGEm virtual-pad
driver, but the game's own input layer maps the d-pad and the arrow keys onto the
same movement commands, so a held arrow key exercises exactly the same scroll
code path as a held d-pad.

This is a test instrument: it exists so the scroll fix can be measured against a
held direction without anyone sitting on a keyboard for 20 seconds.

Usage:
    python pad_drive.py right 6        # hold Right for 6 seconds
    python pad_drive.py list           # list Coromon's windows and give up
"""

import ctypes
import re
import subprocess
import sys
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

VK = {"left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28}
SCAN = {"left": 0x4B, "up": 0x48, "right": 0x4D, "down": 0x50}
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002

PROCESS = "coromon.exe"


def process_pid():
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {PROCESS}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
    ).stdout
    m = re.search(rf'"{re.escape(PROCESS)}","(\d+)"', out, re.I)
    return int(m.group(1)) if m else None


def windows_for_pid(pid):
    """[(hwnd, title, visible)] for every top-level window owned by pid."""
    found = []
    enumproc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        wpid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wpid))
        if wpid.value == pid:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append((hwnd, buf.value, bool(user32.IsWindowVisible(hwnd))))
        return True

    user32.EnumWindows(enumproc(callback), 0)
    return found


def focus(hwnd):
    """SetForegroundWindow is refused unless we share the foreground thread's
    input queue, so attach to it for the duration of the call."""
    fg = user32.GetForegroundWindow()
    tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(user32.AttachThreadInput(tid_fg, tid_me, True))
    try:
        # Only un-minimise, and only when actually minimised. SW_RESTORE also restores a
        # *maximised* window to its original size and position - and Coromon's fullscreen is
        # exactly that - so calling it unconditionally dropped the game out of fullscreen
        # into a 1216x808 window every time we focused it. Measured on the live window:
        # 1936x1060 zoomed=True -> SW_RESTORE -> 1216x808 zoomed=False.
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(tid_fg, tid_me, False)
    return user32.GetForegroundWindow() == hwnd


def key(direction, down):
    flags = KEYEVENTF_EXTENDEDKEY | (0 if down else KEYEVENTF_KEYUP)
    user32.keybd_event(VK[direction], SCAN[direction], flags, 0)


def main():
    pid = process_pid()
    if pid is None:
        print(f"{PROCESS} is not running")
        return 1
    wins = windows_for_pid(pid)
    print(f"{PROCESS} pid={pid}")
    for hwnd, title, vis in wins:
        print(f"  hwnd 0x{hwnd:X}  visible={vis}  title={title!r}")
    if not wins:
        return 1

    if len(sys.argv) > 1 and sys.argv[1] == "list":
        return 0

    direction = sys.argv[1] if len(sys.argv) > 1 else "right"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    if direction not in VK:
        print(f"direction must be one of {sorted(VK)}")
        return 1

    # the visible, titled window is the game; fall back to the first one
    hwnd = next((h for h, t, v in wins if v and t), wins[0][0])
    print(f"focusing hwnd 0x{hwnd:X} -> {focus(hwnd)}")
    time.sleep(0.4)

    print(f"holding {direction} for {seconds:g}s")
    key(direction, True)
    try:
        time.sleep(seconds)
    finally:
        key(direction, False)
    print("released")
    return 0


if __name__ == "__main__":
    sys.exit(main())
