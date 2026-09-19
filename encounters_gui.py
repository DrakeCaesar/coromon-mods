#!/usr/bin/env python3
"""
encounters_gui.py - pick the areas you actually have, and it ranks where to grind.

A small tkinter window over the same data `encounters.py` reads (the game's own
`encounterZones.json`), so nothing needs to be running and nothing is written into the game.

Left: every map that has encounters, as a tick list. Tick the ones you can reach. The tick
list, your squad level and the filters are remembered between runs, in

    %APPDATA%/coromon-grind.json

which is deliberately outside the repository - it is your state, not the tool's source.

Right: every zone in the ticked areas, ranked by the rate-weighted level of what you meet
there, with the species of the selected zone spelled out underneath. "still gives XP" hides
zones whose highest monster is more than 3 levels below your squad, since those are the ones
that stop being worth the walk. "scales" marks zones with a huge level span (Swurmy L1-99 in
the titan temple) which rank high on paper and are not grind spots.

The three things worth knowing about the numbers, all of them also in encounters.py:

  * a species' share is its `stepsWithEncounter` weight over the zone's total weight, because
    those weights do NOT sum to the zone's own `stepsUntilSeenAllEncounters` (measured: equal
    in 0 of 101 zones), so dividing by that field would be wrong;
  * whether the game draws proportionally to those weights is NOT verified - treat the shares
    as relative weights;
  * the monster data carries no XP yield and no level curve, so ranking is by monster level,
    not by XP per battle.

Run it with `--selftest` to build the model and print the top ranking without opening a window.
"""

import argparse
import ctypes
import json
import os
import re
import sys
import tkinter as tk
from tkinter import ttk

try:
    import savefile
except ImportError:                     # the button then explains itself instead of crashing
    savefile = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import encounters  # noqa: E402

STATE_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "coromon-grind.json"
)

# Dark by default: the palette is here so it is one place to change.
BG = "#1e1f22"        # window and frame background
FIELD = "#2b2d30"     # entries, lists, the detail pane
FG = "#dcdcdc"        # text
DIM = "#3a3d41"       # borders and hover
ACCENT = "#3d6ea8"    # selection
NOTE = "#9aa0a6"      # the small print


def apply_dark(root):
    """Colour every widget we use, ttk and plain tk alike.

    The Windows ttk theme ignores most colour options, so the theme is switched to 'clam'
    first - without that, the checkbuttons and the tree keep their light system colours."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                    bordercolor=DIM, lightcolor=BG, darkcolor=BG, troughcolor=FIELD,
                    focuscolor=ACCENT, insertcolor=FG, arrowcolor=FG)
    for name in ("TFrame", "TLabelframe", "TLabel", "TCheckbutton"):
        style.configure(name, background=BG, foreground=FG)
    style.configure("TLabelframe", bordercolor=DIM)
    style.configure("TLabelframe.Label", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)],
              foreground=[("disabled", NOTE)])
    style.configure("TButton", background=FIELD, foreground=FG, bordercolor=DIM,
                    focuscolor=ACCENT)
    style.map("TButton", background=[("active", DIM)], foreground=[("active", FG)])
    style.configure("TEntry", fieldbackground=FIELD, foreground=FG)
    style.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, background=FIELD)
    style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=FG,
                    bordercolor=BG, lightcolor=BG, darkcolor=BG, rowheight=20)
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=BG, foreground=FG, relief="flat",
                    bordercolor=DIM)
    style.map("Treeview.Heading", background=[("active", DIM)])
    style.configure("Vertical.TScrollbar", background=FIELD, troughcolor=BG,
                    bordercolor=BG, arrowcolor=FG)
    style.map("Vertical.TScrollbar", background=[("active", DIM)])
    return style


def pretty(name):
    """waterRoute_3 -> "Water Route 3", frozenCave_4 -> "Frozen Cave 4"."""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).replace("_", " ")
    words = []
    for word in re.sub(r"\s+", " ", text).strip().split(" "):
        m = re.match(r"^([A-Za-z]+)(\d+)$", word)
        if m:
            words.extend([m.group(1)[:1].upper() + m.group(1)[1:], m.group(2)])
        elif word:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1)
    except OSError:
        pass


GEOM_RE = re.compile(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$")
EDGE = 80  # how much of the window must stay grabbable


def desktop_bounds(root):
    """(x, y, width, height) of the whole desktop, not just the primary monitor.

    Tk only ever reports the primary monitor, so a position on a second monitor looks
    off-screen to it and would be dragged back onto the primary. Windows knows the virtual
    desktop, which spans every monitor - and starts at a negative x when one sits to the
    left of the primary.
    """
    if os.name == "nt":
        try:
            gsm = ctypes.windll.user32.GetSystemMetrics
            vx, vy, vw, vh = gsm(76), gsm(77), gsm(78), gsm(79)  # SM_*VIRTUALSCREEN
            if vw > 0 and vh > 0:
                return vx, vy, vw, vh
        except OSError:
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def sanitize_geometry(text, root):
    """A saved "WxH+X+Y", kept somewhere it can actually be reached.

    A position saved under one monitor layout can be off the desktop under another, and a
    window with no visible part cannot be dragged back - so only then is it pulled back into
    the desktop, leaving a grabbable strip. Any position that still lands on a monitor, in
    any direction, is left exactly alone; the size is clamped to the window's minimum.
    """
    m = GEOM_RE.match((text or "").strip())
    if not m:
        return None
    w, h, x, y = (int(g) for g in m.groups())
    w, h = max(w, 820), max(h, 480)
    dx, dy, dw, dh = desktop_bounds(root)
    # reachable == a grabbable strip of the window is on a monitor
    over_x = min(x + w, dx + dw) - max(x, dx)
    over_y = min(y + h, dy + dh) - max(y, dy)
    if over_x < EDGE or over_y < EDGE or y < dy:
        x = min(max(x, dx + EDGE - w), dx + dw - EDGE)
        y = min(max(y, dy), dy + dh - EDGE)
    return "%dx%d+%d+%d" % (w, h, x, y)


def zone_rows(zone, min_share=0.0):
    """The species of a zone, most common first."""
    rows = []
    for uid, rec in sorted(zone.monsters.items(), key=lambda kv: -kv[1]["share"]):
        if rec["share"] < min_share:
            continue
        rows.append({
            "name": zone.species.get(uid, uid),
            "min": rec["min"], "max": rec["max"], "share": rec["share"],
            "battles": rec["battles"],
        })
    return rows


def rank(zones, level, min_share=0.0, only_xp=True):
    """Zones worth walking to for a squad of `level`, best first."""
    picked = []
    for z in zones:
        top = max((r["max"] for r in z.monsters.values()), default=0)
        if only_xp and level and top < level - 3:
            continue
        picked.append(z)
    picked.sort(key=lambda z: -z.average_level)
    return picked


class GrindApp:
    def __init__(self, root, zones, species):
        self.root = root
        self.species = species
        self.by_map = {}
        for z in zones:
            self.by_map.setdefault(z.map_file, []).append(z)
        self.maps = sorted(self.by_map)
        self.state = load_state()

        saved = self.state.get("available")
        self.available = set(saved) if isinstance(saved, list) else set(self.maps)
        self.level = tk.IntVar(value=int(self.state.get("level", 50)))
        self.min_share = tk.DoubleVar(value=float(self.state.get("min_share", 0.0)))
        self.only_xp = tk.BooleanVar(value=bool(self.state.get("only_xp", True)))
        self.on_top = tk.BooleanVar(value=bool(self.state.get("on_top", True)))
        self.search = tk.StringVar(value="")
        self.checks = {}
        self.zone_pick = {}
        self._geom_job = None
        self.geometry = None

        root.title("Coromon - where to grind")
        self.geometry = sanitize_geometry(self.state.get("geometry"), root)
        root.geometry(self.geometry or "1060x640")
        root.minsize(820, 480)
        root.attributes("-topmost", self.on_top.get())
        # <Configure> fires for every pixel of a drag, so the save is debounced
        root.bind("<Configure>", self.on_configure)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.build()
        self.rebuild_list()
        self.refresh()

    # ---------------------------------------------------------------- widgets
    def build(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(outer, text="Areas you have", padding=6)
        left.grid(row=0, column=0, sticky="nsw")
        ttk.Entry(left, textvariable=self.search, width=26).pack(fill="x")
        self.search.trace_add("write", lambda *_: self.rebuild_list())
        row = ttk.Frame(left)
        row.pack(fill="x", pady=3)
        ttk.Button(row, text="All", width=6,
                   command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(row, text="None", width=6,
                   command=lambda: self.set_all(False)).pack(side="left", padx=3)
        self.count_label = ttk.Label(row, text="")
        self.count_label.pack(side="left")

        row2 = ttk.Frame(left)
        row2.pack(fill="x", pady=(0, 2))
        ttk.Button(row2, text="What I've visited",
                   command=self.tick_visited).pack(side="left")
        self.save_label = ttk.Label(left, text="", wraplength=230, justify="left")
        self.save_label.pack(fill="x")

        holder = ttk.Frame(left)
        holder.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(holder, width=240, highlightthickness=0, bd=0,
                                background=BG)
        bar = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self.window, width=e.width))

        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.rowconfigure(1, weight=1)
        right.rowconfigure(3, weight=1)
        right.columnconfigure(0, weight=1)

        controls = ttk.Frame(right)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(controls, text="squad level").pack(side="left")
        spin = ttk.Spinbox(controls, from_=1, to=100, width=4, textvariable=self.level,
                           command=self.refresh)
        spin.pack(side="left", padx=(4, 12))
        spin.bind("<KeyRelease>", lambda e: self.refresh())
        ttk.Checkbutton(controls, text="still gives XP", variable=self.only_xp,
                        command=self.refresh).pack(side="left", padx=(0, 12))
        ttk.Label(controls, text="hide species under %").pack(side="left")
        share = ttk.Spinbox(controls, from_=0, to=100, width=4, textvariable=self.min_share,
                            command=self.refresh)
        share.pack(side="left", padx=4)
        share.bind("<KeyRelease>", lambda e: self.refresh())
        ttk.Checkbutton(controls, text="keep window on top", variable=self.on_top,
                        command=self.apply_on_top).pack(side="right")

        cols = ("area", "zone", "explvl", "best", "flags")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=12)
        for cid, text, width, anchor in (
            ("area", "Area", 190, "w"), ("zone", "Zone", 170, "w"),
            ("explvl", "exp level", 80, "e"), ("best", "most common", 240, "w"),
            ("flags", "flags", 150, "w"),
        ):
            self.tree.heading(cid, text=text, command=lambda c=cid: self.sort_by(c))
            self.tree.column(cid, width=width, anchor=anchor, stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        scroll = ttk.Scrollbar(right, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())

        detail = ttk.LabelFrame(right, text="species in the selected zone", padding=6)
        detail.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.detail = tk.Text(detail, height=8, wrap="none", font=("Consolas", 9),
                              bd=0, highlightthickness=0, background=FIELD, foreground=FG,
                              insertbackground=FG, selectbackground=ACCENT)
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")

        ttk.Label(right, foreground=NOTE, wraplength=760, justify="left", text=(
            "Shares are the game's own encounter weights, normalised - whether the game draws "
            "proportionally to them is unverified. The monster data has no XP yield, so zones "
            "are ranked by how high the monsters are, not by XP per battle."
        )).grid(row=4, column=0, sticky="w", pady=(6, 0))

        self.sort_col, self.sort_desc = "explvl", True

    def on_configure(self, event=None):
        if self._geom_job is not None:
            self.root.after_cancel(self._geom_job)
        self._geom_job = self.root.after(500, self.save_geometry)

    def save_geometry(self):
        self._geom_job = None
        if self.root.state() != "normal":
            # iconified or zoomed: geometry() would store nonsense, so keep the last good one
            return
        self.geometry = self.root.geometry()
        self.persist()

    def on_close(self):
        self.save_geometry()
        self.root.destroy()

    def apply_on_top(self):
        self.root.attributes("-topmost", self.on_top.get())
        self.persist()

    # ---------------------------------------------------------------- area list
    def rebuild_list(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.checks = {}
        needle = self.search.get().strip().lower()
        shown = 0
        for map_file in self.maps:
            label = pretty(map_file)
            if needle and needle not in label.lower() and needle not in map_file.lower():
                continue
            var = tk.BooleanVar(value=map_file in self.available)
            cb = ttk.Checkbutton(self.inner, text=label, variable=var,
                                 command=lambda m=map_file: self.toggle(m))
            cb.pack(anchor="w")
            self.checks[map_file] = var
            shown += 1
        self.count_label.configure(text="%d of %d ticked" % (len(self.available), len(self.maps)))
        self.canvas.yview_moveto(0)
        if shown == 0:
            ttk.Label(self.inner, text="(nothing matches)").pack(anchor="w")

    def set_all(self, on):
        for map_file, var in self.checks.items():
            var.set(on)
            if on:
                self.available.add(map_file)
            else:
                self.available.discard(map_file)
        self.rebuild_list()
        self.refresh()

    def toggle(self, map_file):
        if self.checks[map_file].get():
            self.available.add(map_file)
        else:
            self.available.discard(map_file)
        self.count_label.configure(text="%d of %d ticked" % (len(self.available), len(self.maps)))
        self.persist()
        self.refresh()

    def tick_visited(self):
        """Tick exactly the areas the save says were visited, and nothing else.

        Saves ticking 69 boxes down to the handful you actually have - the save records every
        map walked through, in `settings.VISITED_MAPS` (see savefile).
        """
        if savefile is None:
            self.save_label.configure(text="save: savefile.py is missing")
            return
        try:
            slot, seen = savefile.visited()
            hit = savefile.areas(self.maps, seen)
        except Exception as exc:
            self.save_label.configure(text="save: %s: %s" % (type(exc).__name__, exc))
            return
        self.available = set(hit)
        self.rebuild_list()
        self.refresh()
        self.save_label.configure(
            text="%d of %d areas, from %s (%d maps visited)"
                 % (len(hit), len(self.maps), slot, len(seen)))

    # ---------------------------------------------------------------- ranking
    def current(self):
        picked = [z for m, zs in self.by_map.items() if m in self.available for z in zs]
        try:
            level = int(self.level.get())
        except (tk.TclError, ValueError):
            level = 0
        try:
            min_share = float(self.min_share.get())
        except (tk.TclError, ValueError):
            min_share = 0.0
        return rank(picked, level, min_share, self.only_xp.get()), min_share

    def refresh(self):
        zones, min_share = self.current()
        self.zone_pick = {}
        self.tree.delete(*self.tree.get_children())
        for z in zones:
            rows = zone_rows(z, min_share)
            best = rows[0] if rows else None
            flags = []
            if z.water:
                flags.append("water")
            if max((r["max"] for r in z.monsters.values()), default=0) - \
                    min((r["min"] for r in z.monsters.values()), default=0) >= 30:
                flags.append("scales")
            if any(r["battles"] >= 2 for r in rows):
                flags.append("multi")
            iid = self.tree.insert("", "end", values=(
                pretty(z.map_file), z.name, "%.2f" % z.average_level,
                ("%s L%s-%s  %.0f%%" % (best["name"], best["min"], best["max"], best["share"])
                 if best else ""),
                " ".join(flags),
            ))
            self.zone_pick[iid] = z
        self.sort_tree()
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
        else:
            self.show_empty_hint()
        self.persist()

    def show_empty_hint(self):
        """Say what the filters removed, rather than leaving the ranking blank.

        A tick list covering only early areas plus the "still gives XP" filter is a common
        combination that yields nothing at all, and an empty pane reads like a bug.
        """
        reachable = [z for m, zs in self.by_map.items() if m in self.available for z in zs]
        if not reachable:
            text = ("Nothing is ticked, so there is nothing to rank.\n\nTick an area on the "
                    "left, or press \"What I've visited\".")
        else:
            best = max(reachable, key=lambda z: max(
                (r["max"] for r in z.monsters.values()), default=0))
            top = max((r["max"] for r in best.monsters.values()), default=0)
            try:
                level = int(self.level.get())
            except (tk.TclError, ValueError):
                level = 0
            text = ("No zone here still gives XP at squad level %d.\n\nThe highest monster in "
                    "the ticked areas is L%d, in %s (%s). Untick \"still gives XP\" to rank "
                    "them anyway, or reach further areas." % (level, top, best.name,
                                                             pretty(best.map_file)))
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("end", text)
        self.detail.configure(state="disabled")

    def sort_by(self, col):
        if col == self.sort_col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, col in ("explvl", "best")
        self.sort_tree()

    def sort_tree(self):
        rows = [(self.tree.set(i, self.sort_col), i) for i in self.tree.get_children()]

        def key(item):
            text = item[0]
            try:
                return (0, float(text))
            except ValueError:
                return (1, text.lower())

        rows.sort(key=key, reverse=self.sort_desc)
        for index, (_, iid) in enumerate(rows):
            self.tree.move(iid, "", index)

    def show_detail(self):
        sel = self.tree.selection()
        zone = self.zone_pick.get(sel[0]) if sel else None
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        if zone is not None:
            _, min_share = self.current()
            self.detail.insert("end", "%s  (%s)%s\n\n" % (
                zone.name, pretty(zone.map_file), "   water" if zone.water else ""))
            for r in zone_rows(zone, min_share):
                tag = {1: "", 2: "   double battle", 3: "   triple battle"}.get(r["battles"], "")
                self.detail.insert("end", "  %-14s L%-3s-%-3s  %5.1f%%%s\n" % (
                    r["name"], r["min"], r["max"], r["share"], tag))
        self.detail.configure(state="disabled")

    def persist(self):
        # a half-typed spinbox must not be able to raise out of a UI callback
        try:
            state = {
                "available": sorted(self.available),
                "level": self.level.get(),
                "min_share": self.min_share.get(),
                "only_xp": self.only_xp.get(),
                "on_top": self.on_top.get(),
                "geometry": self.geometry,
            }
            if not self.geometry:
                # persist() also runs during start-up, before the window has a position;
                # writing None there would throw away a perfectly good saved one
                state.pop("geometry", None)
        except (tk.TclError, ValueError):
            return
        save_state(state)


def selftest():
    zones, species = encounters.load()
    zs = encounters.all_zones(zones, species)
    print("zones:", len(zs), "species:", len(species))
    print("state file:", STATE_PATH, "(exists)" if os.path.exists(STATE_PATH) else "(not yet)")
    for z in rank(zs, 64, only_xp=True)[:6]:
        rows = zone_rows(z)
        print("  %-22s %-18s expLvl %5.2f  %s" % (
            z.name, pretty(z.map_file), z.average_level,
            ", ".join("%s L%s-%s %.0f%%" % (r["name"], r["min"], r["max"], r["share"])
                      for r in rows[:3])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pick the areas you have; it ranks where to grind.")
    ap.add_argument("--selftest", action="store_true",
                    help="build the model and print the ranking without opening a window")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    zones, species = encounters.load()
    root = tk.Tk()
    apply_dark(root)
    GrindApp(root, encounters.all_zones(zones, species), species)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
