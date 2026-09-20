#!/usr/bin/env python3
"""
gui_theme.py - the dark palette and the window helpers both GUIs share.

`encounters_gui.py` grew its own copy of this first; it is factored out here rather than
changed, so that nothing about a working tool moves. New GUIs should import from here.

The palette is in one place on purpose - it is the only thing about a tkinter window that
has to be repeated across every widget class, and the reason the theme is applied rather
than set per-widget is that the Windows ttk theme ignores most colour options. Switching to
'clam' is what makes the rest of it take effect.
"""

import ctypes
import re
import tkinter as tk
from tkinter import ttk

BG = "#1e1f22"        # window and frame background
FIELD = "#2b2d31"     # inputs, lists
FG = "#dcdcdc"        # text
DIM = "#4a4d52"       # borders, inactive
ACCENT = "#3d6ea8"    # selection
NOTE = "#9aa0a6"      # secondary text
OK = "#7ac47a"        # a running controller
BAD = "#d9736a"       # stopped, or a failure


def apply_dark(root):
    """Colour every widget class the GUIs use, ttk and plain tk alike."""
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                    bordercolor=DIM, lightcolor=BG, darkcolor=BG, troughcolor=FIELD,
                    focuscolor=ACCENT, insertcolor=FG, arrowcolor=FG)
    for name in ("TFrame", "TLabel", "TLabelframe", "TCheckbutton", "TRadiobutton"):
        style.configure(name, background=BG, foreground=FG)
    style.configure("TLabelframe.Label", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", FG)])
    style.configure("TButton", background=FIELD, foreground=FG, bordercolor=DIM,
                    lightcolor=FIELD, darkcolor=FIELD, focuscolor=ACCENT)
    style.map("TButton", background=[("active", DIM)], foreground=[("active", FG)])
    style.configure("TEntry", fieldbackground=FIELD, foreground=FG)
    style.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, background=FIELD,
                    arrowcolor=FG)
    style.configure("TCombobox", fieldbackground=FIELD, foreground=FG, background=FIELD,
                    arrowcolor=FG)
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)],
              foreground=[("readonly", FG)])
    style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=FG,
                    bordercolor=BG, lightcolor=BG, darkcolor=BG, rowheight=20)
    style.map("Treeview", background=[("selected", ACCENT)])
    style.configure("Treeview.Heading", background=BG, foreground=FG, relief="flat")
    style.configure("Vertical.TScrollbar", background=FIELD, troughcolor=BG,
                    bordercolor=BG, arrowcolor=FG)
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    return style


def desktop_bounds(root):
    """The whole virtual desktop, in pixels, as (left, top, right, bottom).

    Not `root.winfo_screenwidth()`: that reports the PRIMARY monitor only, so a window saved
    on a second screen looks off-screen to it and gets "rescued" onto the first one - which
    is exactly the bug that made a remembered position feel like it was being ignored.
    """
    try:
        gsm = ctypes.windll.user32.GetSystemMetrics
        left, top = gsm(76), gsm(77)
        width, height = gsm(78), gsm(79)
        if width > 0 and height > 0:
            return left, top, left + width, top + height
    except Exception:                                  # noqa: BLE001 - non-Windows, or no user32
        pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


_GEOM = re.compile(r"^(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?$")


def sanitize_geometry(text, root, edge=80):
    """A geometry string that is worth restoring, or None.

    A remembered position is only useful if the window can be reached. A size is kept
    whatever the position, and a position is kept as long as at least `edge` pixels of the
    title bar land somewhere on the virtual desktop - which is deliberately permissive: this
    is here to rescue a window lost off-screen, not to second-guess where it was put.
    """
    if not text:
        return None
    m = _GEOM.match(str(text).strip())
    if not m:
        return None
    w, h = int(m.group(1)), int(m.group(2))
    if w < 200 or h < 150:
        return None
    if m.group(3) is None:
        return "%dx%d" % (w, h)
    x, y = int(m.group(3)), int(m.group(4))
    left, top, right, bottom = desktop_bounds(root)
    reachable = (x + w - edge >= left and x + edge <= right
                 and y + edge >= top and y + edge <= bottom)
    if not reachable:
        return "%dx%d" % (w, h)
    return "%dx%d%+d%+d" % (w, h, x, y)
