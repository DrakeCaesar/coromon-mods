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

The window has two tabs. "Where to grind" is everything described above, and a third column
beside the ranking draws the AREA the selected row belongs to, with the game's own zone letters on
it - which patch of grass "Grass B" is, which the wiki never says. The species of the selected
zone sit under that map. "Skills" is a
second view over the game's own `skills.json` - all 258 of them, in the columns the wiki's
skill table uses, with the full description of whichever one is selected underneath. It
shares nothing with the ranking except the window: it needs no save, no game and no areas
ticked, so it is there whether or not the left-hand list is set up. The data and the wording
of every column and description live in `skills.py`, which is also runnable on its own.
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
import encounter_zones as ez  # noqa: E402
import skills  # noqa: E402

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
MAP_GROUND = "#101114"  # the solid ground the zone colours sit on, in the map tab


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
    style.configure("TNotebook", background=BG, bordercolor=DIM, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=BG, foreground=NOTE, padding=(10, 5),
                    bordercolor=DIM)
    style.map("TNotebook.Tab", background=[("selected", FIELD), ("active", DIM)],
              foreground=[("selected", FG)])
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


def sort_tree_rows(tree, col, desc):
    """Sort a Treeview by one column, numbers numerically and blanks always at the bottom.

    Both tables here have columns that are numbers for some rows and "-" for others (a status
    skill has no power, a zone has no "most common" species when the share filter hid them).
    Sorting those mixed rows as text puts "-" above "95" ascending and above it descending too,
    so numbers and non-numbers are ranked in two groups and the non-numbers are appended last
    whichever way round the sort is.
    """
    numbers, others = [], []
    for iid in tree.get_children():
        value = tree.set(iid, col)
        try:
            numbers.append((float(value), iid))
        except ValueError:
            others.append((value.lower(), iid))
    numbers.sort(reverse=desc)
    others.sort(reverse=desc)
    for index, (_, iid) in enumerate(numbers + others):
        tree.move(iid, "", index)


class GrindApp:
    def __init__(self, root, zones, species, skill_list):
        self.root = root
        self.species = species
        self.skills = skill_list
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
        self.selected_zone = None     # what the map tab draws
        self.skill_pick = {}
        self._geom_job = None
        self.geometry = None

        root.title("Coromon - grind & skills")
        self.geometry = sanitize_geometry(self.state.get("geometry"), root)
        root.geometry(self.geometry or "1420x760")
        root.minsize(1040, 560)
        root.attributes("-topmost", self.on_top.get())
        # <Configure> fires for every pixel of a drag, so the save is debounced
        root.bind("<Configure>", self.on_configure)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.build()
        self.rebuild_list()
        self.refresh()
        self.refresh_skills()
        saved_tab = self.state.get("tab")
        if isinstance(saved_tab, int) and 0 <= saved_tab < len(self.notebook.tabs()):
            self.notebook.select(saved_tab)

    # ---------------------------------------------------------------- widgets
    def build(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 6))
        self.notebook.bind("<<NotebookTabChanged>>", self.on_tab_changed)

        grind_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(grind_tab, text="  Where to grind  ")

        outer = ttk.Frame(grind_tab)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=0)
        outer.columnconfigure(1, weight=1)                # the ranking
        outer.columnconfigure(2, weight=1, minsize=360)   # the map, with the species under it
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

        middle = ttk.Frame(outer)
        middle.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        middle.rowconfigure(1, weight=1)
        middle.columnconfigure(0, weight=1)

        controls = ttk.Frame(middle)
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
        self.tree = ttk.Treeview(middle, columns=cols, show="headings", height=12)
        for cid, text, width, anchor in (
            ("area", "Area", 190, "w"), ("zone", "Zone", 170, "w"),
            ("explvl", "exp level", 80, "e"), ("best", "most common", 240, "w"),
            ("flags", "flags", 150, "w"),
        ):
            self.tree.heading(cid, text=text, command=lambda c=cid: self.sort_by(c))
            self.tree.column(cid, width=width, anchor=anchor, stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        scroll = ttk.Scrollbar(middle, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.show_detail())

        # the small print stays under the ranking, next to the numbers it explains
        ttk.Label(middle, foreground=NOTE, wraplength=700, justify="left", text=(
            "Shares are the game's own encounter weights, normalised - whether the game draws "
            "proportionally to them is unverified. The monster data has no XP yield, so zones "
            "are ranked by how high the monsters are, not by XP per battle."
        )).grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

        # the map, then the species under it: WHICH patch of grass a zone is belongs next to the
        # ranking that made you ask, and the species of the selected zone belong under the map
        # because they are what the map is being read for
        right = ttk.Frame(outer)
        right.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=3)
        right.rowconfigure(4, weight=2)
        self.build_map(right)

        detail = ttk.LabelFrame(right, text="species in the selected zone", padding=6)
        detail.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        self.detail = tk.Text(detail, height=8, wrap="none", font=("Consolas", 9),
                              bd=0, highlightthickness=0, background=FIELD, foreground=FG,
                              insertbackground=FG, selectbackground=ACCENT)
        self.detail.pack(fill="both", expand=True)
        self.detail.configure(state="disabled")

        self.sort_col, self.sort_desc = "explvl", True

        skills_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(skills_tab, text="  Skills  ")
        self.build_skills(skills_tab)

    # ---------------------------------------------------------------- zone map
    def build_map(self, parent):
        """WHERE the zone selected in the ranking actually is - the right-hand column.

        The wiki lists rates per area as "Grass A / Grass B / ..." and never says which patch each
        one is. The game's maps do: a patch carries a `grassArea` marker whose zoneUID IS that
        letter. So this draws the area with its zones coloured and the selected row picked out,
        beside the ranking rather than on a tab of its own - the question only comes up while
        looking at the ranking.
        """
        self.map_head = ttk.Label(parent, text="", wraplength=420, justify="left")
        self.map_head.grid(row=0, column=0, sticky="w")

        self.map_legend = ttk.Frame(parent)
        self.map_legend.grid(row=1, column=0, sticky="w", pady=(3, 4))

        self.map_canvas = tk.Canvas(parent, highlightthickness=0, bd=0, background=FIELD)
        self.map_canvas.grid(row=2, column=0, sticky="nsew")
        self.map_canvas.bind("<Configure>", lambda e: self.redraw_map())

        ttk.Label(parent, foreground=NOTE, wraplength=420, justify="left", text=(
            "Patches are read from the map tiles: the marker's tile, plus the connected tiles of "
            "the same tileset - exact for grass, whose tiles are one map cell. Water and cave "
            "markers name no layer and show as small outlined squares."
        )).grid(row=3, column=0, sticky="w", pady=(6, 0))

    def redraw_map(self):
        """Draw the selected zone's area. Silent while the tab is not on screen."""
        canvas = self.map_canvas
        if not canvas.winfo_ismapped():
            return
        canvas.delete("all")
        for child in self.map_legend.winfo_children():
            child.destroy()

        zone = self.selected_zone
        if zone is None:
            self.map_head.configure(text="pick a zone on the first tab")
            return
        data = ez.zone_map(zone.map_file)
        if data is None:
            self.map_head.configure(text="no map file found for %s" % zone.map_file)
            return
        m, layers, zones = data
        drawn = {n: v for n, v in zones.items() if v["patches"] or v["unplaced"]}
        entry = drawn.get(zone.name)
        if entry is None:
            self.map_head.configure(text=(
                "%s is not marked on the map of %s - it has %s"
                % (zone.name, pretty(zone.map_file), ", ".join(sorted(drawn)) or "no zones")))
            return

        # fit the map into whatever the tab got: 8x is the ceiling, because a small map blown up to
        # fill a maximised window is all block
        mw, mh = m["width"], m["height"]
        scale = max(1.0, min(canvas.winfo_width() / mw, canvas.winfo_height() / mh, 8.0))

        for (y, x0, x1) in ez.runs_by_row(ez.cells_by_row(ez.terrain_cells(m, layers))):
            canvas.create_rectangle(x0 * scale, y * scale, x1 * scale, (y + 1) * scale,
                                    fill=MAP_GROUND, outline="")

        for name in sorted(drawn):
            other = drawn[name]
            colour = ez.colour_for(name)
            selected = name == zone.name
            # solid for the selected zone and stippled for the rest: Tk has no alpha, and a patch
            # you cannot pick out of six is not an answer
            stipple = "" if selected else "gray50"
            for tiles in other["patches"]:
                for (y, x0, x1) in ez.runs_by_row(ez.cells_by_row(tiles)):
                    canvas.create_rectangle(x0 * scale, y * scale, x1 * scale, (y + 1) * scale,
                                            fill=colour, outline="", stipple=stipple)
            for i, tiles in enumerate(other["patches"]):
                xs = [t[0] for t in tiles]
                ys = [t[1] for t in tiles]
                size = max(7, int(scale * (2.2 if (selected and i == 0) else 1.4)))
                canvas.create_text((min(xs) + max(xs) + 1) / 2 * scale,
                                   (min(ys) + max(ys) + 1) / 2 * scale,
                                   text=name.rsplit("_", 1)[-1], fill="#ffffff",
                                   font=("Consolas", size, "bold"))
            for (x, y, why) in other["unplaced"]:
                # a fixed size, not the map scale: a one-tile mark on a small scale is a single
                # pixel, and cave and water zones can be nothing BUT these marks
                cx, cy = (x + 0.5) * scale, (y + 0.5) * scale
                canvas.create_rectangle(cx - 2, cy - 2, cx + 2, cy + 2, outline=colour, width=1)

        placed = sum(len(p) for p in entry["patches"])
        spots = ", ".join("(%d,%d)" % (min(t[0] for t in p), min(t[1] for t in p))
                          for p in entry["patches"])
        if entry["patches"]:
            text = "%s   %d patch(es), %d tiles   at %s" % (
                zone.name, len(entry["patches"]), placed, spots or "-")
            if entry["unplaced"]:
                text += "   - %d marker(s) not placed" % len(entry["unplaced"])
        else:
            # NOT a failure to report as one: these zones mark a tile with no layer of its own, so
            # the tiles cannot say what shape the zone is. The marks still say where it is.
            text = ("%s   the map gives %d marker(s) with no tile layer, so only their spots are "
                    "known - they are the small squares" % (zone.name, len(entry["unplaced"])))
        self.map_head.configure(text=text)

        for name in sorted(drawn):
            tk.Label(self.map_legend, text=name.rsplit("_", 1)[-1], bg=ez.colour_for(name),
                     fg="#ffffff", width=3,
                     relief="solid" if name == zone.name else "flat", bd=1).pack(side="left", padx=1)
        ttk.Label(self.map_legend, text="   same colour as the tool's map page; the selected zone "
                                        "is the solid one", foreground=NOTE).pack(side="left")

    def build_skills(self, tab):
        """The second tab: every skill in the wiki's columns, with a full description.

        Nothing here reads the save or the tick list - it is the whole skill list all the
        time, filtered only by what is typed below.
        """
        self.skill_search = tk.StringVar(value=str(self.state.get("skill_search", "")))
        self.skill_type = tk.StringVar(value=str(self.state.get("skill_type", "all")))
        self.skill_sort = str(self.state.get("skill_sort", "name"))
        self.skill_desc = bool(self.state.get("skill_desc", False))

        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        tab.rowconfigure(2, weight=1)

        bar = ttk.Frame(tab)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(bar, text="find").pack(side="left")
        ttk.Entry(bar, textvariable=self.skill_search, width=26).pack(side="left", padx=(4, 14))
        self.skill_search.trace_add("write", lambda *_: self.refresh_skills())
        ttk.Label(bar, text="type").pack(side="left")
        type_box = ttk.Combobox(
            bar, textvariable=self.skill_type, width=12, state="readonly",
            values=["all"] + [skills.type_name(t) for t in skills.types(self.skills)])
        type_box.pack(side="left", padx=4)
        type_box.bind("<<ComboboxSelected>>", lambda e: self.refresh_skills())
        self.skill_count = ttk.Label(bar, text="")
        self.skill_count.pack(side="right")

        cols = tuple(key for key, _, _, _ in skills.COLUMNS)
        self.skill_tree = ttk.Treeview(tab, columns=cols, show="headings", height=13)
        for key, heading, width, anchor in skills.COLUMNS:
            self.skill_tree.heading(key, text=heading, command=lambda c=key: self.sort_skills(c))
            self.skill_tree.column(key, width=width, anchor=anchor, stretch=False)
        self.skill_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        scroll = ttk.Scrollbar(tab, orient="vertical", command=self.skill_tree.yview)
        self.skill_tree.configure(yscrollcommand=scroll.set)
        scroll.grid(row=1, column=1, sticky="ns", pady=(6, 0))
        self.skill_tree.bind("<<TreeviewSelect>>", lambda e: self.show_skill())

        detail = ttk.LabelFrame(tab, text="the selected skill", padding=6)
        detail.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self.skill_detail = tk.Text(detail, height=9, wrap="word", font=("Consolas", 9),
                                    bd=0, highlightthickness=0, background=FIELD, foreground=FG,
                                    insertbackground=FG, selectbackground=ACCENT)
        self.skill_detail.pack(fill="both", expand=True)
        self.skill_detail.configure(state="disabled")

    def on_tab_changed(self, event=None):
        self.persist()
        # the map is only drawn once it is on screen: an unmapped canvas reports a 1-pixel size, so
        # drawing early would pick a nonsense scale and sit there looking broken
        self.redraw_map()

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
        self.selected_zone = None
        self.redraw_map()

    def sort_by(self, col):
        if col == self.sort_col:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_col, self.sort_desc = col, col in ("explvl", "best")
        self.sort_tree()

    def sort_tree(self):
        sort_tree_rows(self.tree, self.sort_col, self.sort_desc)

    def show_detail(self):
        sel = self.tree.selection()
        zone = self.zone_pick.get(sel[0]) if sel else None
        self.selected_zone = zone
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
        self.redraw_map()

    # ---------------------------------------------------------------- skills tab
    def refresh_skills(self):
        picked = skills.shown(self.skills, self.skill_type.get(), self.skill_search.get())
        self.skill_pick = {}
        self.skill_tree.delete(*self.skill_tree.get_children())
        for skill in picked:
            cells = skills.row(skill)
            iid = self.skill_tree.insert(
                "", "end", values=tuple(cells[key] for key, _, _, _ in skills.COLUMNS))
            self.skill_pick[iid] = skill
        sort_tree_rows(self.skill_tree, self.skill_sort, self.skill_desc)
        self.skill_count.configure(text="%d of %d skills" % (len(picked), len(self.skills)))
        children = self.skill_tree.get_children()
        if children:
            self.skill_tree.selection_set(children[0])
        else:
            self.show_skill()
        self.persist()

    def sort_skills(self, col):
        if col == self.skill_sort:
            self.skill_desc = not self.skill_desc
        else:
            # highest first for the three numeric columns, A-Z for the rest
            self.skill_sort, self.skill_desc = col, col in ("sp", "power", "acc")
        sort_tree_rows(self.skill_tree, self.skill_sort, self.skill_desc)
        self.persist()

    def show_skill(self):
        sel = self.skill_tree.selection()
        skill = self.skill_pick.get(sel[0]) if sel else None
        self.skill_detail.configure(state="normal")
        self.skill_detail.delete("1.0", "end")
        if skill is not None:
            self.skill_detail.insert("end", skills.describe(skill))
        self.skill_detail.configure(state="disabled")

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
                "tab": self.notebook.index("current"),
                "skill_search": self.skill_search.get(),
                "skill_type": self.skill_type.get(),
                "skill_sort": self.skill_sort,
                "skill_desc": self.skill_desc,
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
    skill_list = skills.load()
    print("skills:", len(skill_list), "in", len(skills.types(skill_list)), "types")
    for skill in skills.shown(skill_list, "poison")[:2]:
        print("  ", "  ".join("%s=%s" % (k, v) for k, v in skills.row(skill).items()))
        print("    ", skills.resolve(skill.get("description"), skill))
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
    GrindApp(root, encounters.all_zones(zones, species), species, skills.load())
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
