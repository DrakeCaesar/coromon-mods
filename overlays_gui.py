#!/usr/bin/env python3
"""
overlays_gui.py - one window to switch the overlay features on and off, and to run them.

The command line tool is `overlays.py`, and it is deliberately settings-file-driven: there
are no options, and a run makes the game match `overlays.toml`. That is the right shape for
its author and the wrong shape for anybody else - sharing it means handing over a set of Lua
features, a Frida dependency and a TOML file whose section names are internal. So this is the
friendlier face on the same thing:

    * every feature from `ingame/` gets a checkbox, and every setting of every feature gets
      the right kind of control, built from each module's own SETTINGS declaration - so a new
      feature appears here with no change to this file;
    * the file is still the single source of truth. This reads it, edits it and writes it
      back with `config.save`, which regenerates the comments from the schema, so the
      explanations survive a GUI edit;
    * it starts and stops the overlay itself, so there is one thing to run and the log is
      right there in the window.

"Save & Apply" restarts the overlay, because the overlay only re-reads the settings when it
starts and when the game is relaunched - so editing the file alone would appear to do nothing
until the game was restarted. Restarting the overlay is cheap: it re-attaches and reinstalls
straight away if the game is already up.

TWO WAYS IT RUNS, and the second is what makes a single-file build possible:

    overlays_gui.exe                the window
    overlays_gui.exe --controller   the overlay itself, in this same process

A frozen one-file build has no `overlays.py` on disk to spawn, so instead the exe runs itself
with `--controller` and that half does the work `overlays.py` normally does. Run from source
it spawns the real `overlays.py`, so the two stay interchangeable.

`overlays.toml` lives beside the program - beside the .exe when frozen, because a one-file
build unpacks into a temp directory that is deleted on exit and the settings have to outlive
that. See `core._data_dir`.
"""

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gui_theme as theme                                  # noqa: E402
from ingame import FEATURES, config, core                  # noqa: E402

# Where the program's own files are: the source directory, or the .exe's directory when
# frozen. Used for `cwd` and for finding overlays.py when running from source.
if getattr(sys, "frozen", False):
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))

STATE_PATH = os.path.join(os.path.dirname(core.CONFIG_PATH), "overlays_gui_state.json")


def _norm(entry):
    """A SETTINGS entry as (key, default, comment, choices). Duplicated from ingame.config
    rather than reaching for its private name - it is one line and it keeps the coupling to
    the documented shape of a SETTINGS entry instead of to an implementation detail."""
    return entry[0], entry[1], entry[2], (entry[3] if len(entry) > 3 else None)


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
    except OSError:
        pass


def _load_runtime():
    """The runtime overrides, or {} - see ingame/config.py.

    Never fatal: this file is rewritten every time a live checkbox moves, so an unreadable one
    must not stop the window opening. It is reported and treated as "no overrides".
    """
    try:
        return config.load_runtime(config.runtime_path(core.CONFIG_PATH))
    except config.ConfigError as exc:
        print("runtime overrides: %s" % exc, file=sys.stderr)
        return {}


class App:
    def __init__(self, root):
        self.root = root
        self.state = load_state()
        self.vars = {}          # (section, key) -> tk variable
        self.defaults = {}      # (section, key) -> default, to convert back by type
        self.runtime_vars = {}  # feature -> the LIVE switch's tk variable
        self.runtime = _load_runtime()
        self.proc = None
        self.log_path = None
        self.out = queue.Queue()
        self.geometry = None

        self.cfg, self.warnings = self._read_config()

        root.title("Coromon overlays")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.geometry = theme.sanitize_geometry(self.state.get("geometry"), root)
        root.geometry(self.geometry or "980x680")
        root.minsize(760, 480)

        self._build()
        self._log_startup()
        self._poll()

        # remember where it was, but only once it has settled - saving on every resize event
        # would write the file dozens of times while a window is being dragged
        root.after(600, self._save_geometry)

    # ------------------------------------------------------------------ config
    def _read_config(self):
        try:
            cfg, warnings, _created = config.load(
                core.CONFIG_PATH, core.CORE_SETTINGS, FEATURES)
            return cfg, list(warnings)
        except config.ConfigError as exc:
            return None, ["%s" % exc]

    # ------------------------------------------------------------------ widgets
    def _build(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        head = ttk.Frame(outer)
        head.pack(fill="x", pady=(0, 6))
        ttk.Label(head, text="Coromon overlays",
                  font=("Segoe UI", 12, "bold")).pack(side="left")
        ttk.Label(head, foreground=theme.NOTE, text=(
            "   `enabled` is the baseline the tool installs from; `runtime` is what is installed "
            "right now and applies to the running overlay immediately").rstrip()).pack(side="left")

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.LabelFrame(body, text="overlay log", padding=6)
        right.pack(side="left", fill="both", expand=False, padx=(8, 0))

        self._build_settings(left)
        self._build_log(right)

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="Save", command=self.save_only).pack(side="left")
        ttk.Button(bar, text="Save & Apply", command=self.save_and_apply).pack(
            side="left", padx=6)
        ttk.Button(bar, text="Reload from file", command=self.reload).pack(side="left")
        ttk.Button(bar, text="Clear runtime", command=self.clear_runtime).pack(
            side="left", padx=6)
        self.status = ttk.Label(bar, text="overlay stopped", foreground=theme.BAD)
        self.status.pack(side="left", padx=14)
        ttk.Button(bar, text="Stop", command=self.stop_controller).pack(side="right")
        ttk.Button(bar, text="Start", command=self.start_controller).pack(
            side="right", padx=6)

    def _build_settings(self, parent):
        canvas = tk.Canvas(parent, background=theme.BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        window = canvas.create_window((0, 0), window=inner, anchor="nw")

        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        for widget in (canvas, inner):
            widget.bind("<MouseWheel>",
                        lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        if self.cfg is None:
            ttk.Label(inner, foreground=theme.BAD, wraplength=700, justify="left",
                      text="overlays.toml could not be read, so there is nothing to edit:\n"
                           + "\n".join(self.warnings)).pack(anchor="w", padx=6, pady=6)
            return

        self._section(inner, "core", "general", core.CORE_SETTINGS,
                      self.cfg.get("core", {}))
        for f in FEATURES:
            label = f.__doc__.strip().splitlines()[0] if f.__doc__ else f.NAME
            self._section(inner, f.NAME, "%s  -  %s" % (f.NAME, label), f.SETTINGS,
                          self.cfg.get(f.NAME, {}))

    def _section(self, parent, name, title, settings, values):
        box = ttk.LabelFrame(parent, text=title, padding=6)
        box.pack(fill="x", padx=4, pady=4)

        # `enabled` first and on its own line: for a feature it is the switch everything else
        # depends on, so it should not be one control among twenty
        ordered = sorted(settings, key=lambda e: (e[0] != "enabled",))
        for entry in ordered:
            key, default, comment, choices = _norm(entry)
            self.defaults[(name, key)] = default
            if key == "enabled" and name != "core":
                self._enabled_row(box, name, comment, values.get(key, default))
                continue
            self._row(box, name, key, default, comment, choices,
                      values.get(key, default))

    def _enabled_row(self, parent, name, comment, value):
        """The baseline switch and the live one, side by side.

        They answer different questions, which is why there are two of them. `enabled` is what
        the file says - the baseline the tool installs from. `runtime` is what is installed RIGHT
        NOW: moving it writes the runtime override file, which a running overlay picks up within
        half a second, and the baseline file is not touched.

        That is the whole point of having both. Switching a feature off to see what it costs
        becomes one checkbox, not an edit to the file and then a second edit to put it back -
        and a forgotten second edit is indistinguishable from a decision.
        """
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)

        base = tk.BooleanVar(master=self.root, value=bool(value))
        ttk.Checkbutton(row, text="enabled", variable=base).pack(side="left")
        self.vars[(name, "enabled")] = base

        # absent from the override file means "follow the baseline", so that is the value the
        # live box starts at - and a feature nobody has touched shows the baseline in both
        override = (self.runtime.get("enabled") or {}).get(name)
        live = tk.BooleanVar(master=self.root,
                             value=bool(value) if override is None else bool(override))
        ttk.Checkbutton(row, text="runtime", variable=live,
                        command=self.write_runtime).pack(side="left", padx=(10, 0))
        self.runtime_vars[name] = live

        ttk.Label(row, foreground=theme.NOTE, text="  " + comment, wraplength=500,
                  justify="left").pack(side="left")

    def write_runtime(self):
        """Write the runtime layer, recording only what DIFFERS from the baseline.

        A feature that matches its baseline gets no entry at all, so following the file stays
        the default and deleting the override file is a complete reset - there is no way to
        leave a feature permanently pinned by a stale entry.
        """
        flags = {}
        for f in FEATURES:
            live = self.runtime_vars.get(f.NAME)
            base = self.vars.get((f.NAME, "enabled"))
            if live is None or base is None:
                continue
            if bool(live.get()) != bool(base.get()):
                flags[f.NAME] = bool(live.get())
        self.runtime = {"enabled": flags}
        try:
            config.save_runtime(config.runtime_path(core.CONFIG_PATH), flags)
        except OSError as exc:
            self.status.configure(text="runtime: %s" % exc, foreground=theme.BAD)
            return
        off = sorted(n for n, v in flags.items() if not v)
        on = sorted(n for n, v in flags.items() if v)
        self.status.configure(
            text="runtime - off: %s   on: %s" % (", ".join(off) or "none",
                                                ", ".join(on) or "none"),
            foreground=theme.NOTE)

    def clear_runtime(self):
        """Drop every override, putting each live box back on its baseline."""
        for name, live in self.runtime_vars.items():
            base = self.vars.get((name, "enabled"))
            if base is not None:
                live.set(bool(base.get()))
        self.write_runtime()

    def _row(self, parent, section, key, default, comment, choices, value):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)

        if isinstance(default, bool):
            var = tk.BooleanVar(master=self.root, value=bool(value))
            text = key if key != "enabled" else "enabled"
            ttk.Checkbutton(row, text=text, variable=var).pack(side="left")
            self.vars[(section, key)] = var
            ttk.Label(row, foreground=theme.NOTE, text="  " + comment,
                      wraplength=560, justify="left").pack(side="left")
            return

        ttk.Label(row, text=key, width=16).pack(side="left")
        if choices:
            var = tk.StringVar(master=self.root, value=str(value))
            box = ttk.Combobox(row, textvariable=var, values=[str(c) for c in choices],
                               state="readonly", width=14)
            box.pack(side="left")
        elif isinstance(default, int):
            var = tk.StringVar(master=self.root, value=str(value))
            ttk.Spinbox(row, textvariable=var, from_=-1000000, to=1000000, width=10,
                        increment=1).pack(side="left")
        elif isinstance(default, list):
            var = tk.StringVar(master=self.root,
                               value=", ".join(str(v) for v in (value or [])))
            ttk.Entry(row, textvariable=var, width=26).pack(side="left")
        else:
            var = tk.StringVar(master=self.root, value=str(value))
            ttk.Entry(row, textvariable=var, width=26).pack(side="left")
        self.vars[(section, key)] = var
        ttk.Label(row, foreground=theme.NOTE, text="  " + comment, wraplength=520,
                  justify="left").pack(side="left", fill="x", expand=True)

    def _build_log(self, parent):
        self.log = tk.Text(parent, width=42, height=28, wrap="word", bd=0,
                           highlightthickness=0, background=theme.FIELD,
                           foreground=theme.FG, insertbackground=theme.FG,
                           selectbackground=theme.ACCENT, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        bar = ttk.Scrollbar(parent, orient="vertical", command=self.log.yview)
        bar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=bar.set)

    # ------------------------------------------------------------------ logging
    def log_line(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip("\n") + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_startup(self):
        self.log_line("settings file: %s" % core.CONFIG_PATH)
        if self.cfg is None:
            self.log_line("could not read it - fix the file, then Reload from file")
        for w in self.warnings:
            self.log_line("config: %s" % w)
        if not self.warnings:
            self.log_line("settings read without complaint")

    # ------------------------------------------------------------------ save
    def gather(self):
        """The widgets as a config dict. Raises ValueError naming the field that is wrong,
        so a typo is reported next to the button rather than as a stack trace."""
        out = {"core": {}}
        for (section, key), var in self.vars.items():
            default = self.defaults[(section, key)]
            if isinstance(default, bool):
                value = bool(var.get())
            elif isinstance(default, int):
                text = str(var.get()).strip()
                try:
                    value = int(text)
                except ValueError:
                    raise ValueError("%s.%s: %r is not a whole number" % (section, key, text))
            elif isinstance(default, list):
                raw = str(var.get())
                value = [p.strip() for p in raw.split(",") if p.strip()]
                if not value:
                    raise ValueError("%s.%s: give at least one value" % (section, key))
            else:
                value = str(var.get())
            out.setdefault(section, {})[key] = value
        return out

    def save_only(self):
        try:
            cfg = self.gather()
        except ValueError as exc:
            self.log_line("not saved: %s" % exc)
            return None
        config.save(core.CONFIG_PATH, core.CORE_SETTINGS, FEATURES, cfg)
        self.cfg, self.warnings = self._read_config()
        self.log_line("saved %s" % os.path.basename(core.CONFIG_PATH))
        for w in self.warnings:
            self.log_line("config: %s" % w)
        return self.cfg

    def save_and_apply(self):
        if self.save_only() is None:
            return
        self.log_line("restarting the overlay so the change takes effect")
        self.stop_controller(quiet=True)
        self.start_controller()

    def reload(self):
        self.cfg, self.warnings = self._read_config()
        for (section, key), var in list(self.vars.items()):
            if self.cfg is None:
                continue
            value = self.cfg.get(section, {}).get(key)
            if value is None:
                continue
            default = self.defaults[(section, key)]
            if isinstance(default, bool):
                var.set(bool(value))
            elif isinstance(default, list):
                var.set(", ".join(str(v) for v in value))
            else:
                var.set(str(value))
        self.log_line("reloaded %s" % os.path.basename(core.CONFIG_PATH))
        for w in self.warnings:
            self.log_line("config: %s" % w)

    # ------------------------------------------------------------------ controller
    def controller_argv(self, log_path):
        """How to run the overlay.

        Always this same program with --controller, so a run from source and a run from the
        .exe take the identical path - the only difference is which executable is re-invoked.
        (`overlays.py` stays the command-line equivalent for the author; the GUI does not use
        it, because it has no way to be told where to write its log.)
        """
        if getattr(sys, "frozen", False):
            return [sys.executable, "--controller", "--log", log_path]
        return [sys.executable, os.path.join(BASE, "overlays_gui.py"),
                "--controller", "--log", log_path]

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start_controller(self):
        if self.running():
            self.log_line("already running")
            return
        self.log_path = os.path.join(tempfile.gettempdir(), "coromon-overlays-gui.log")
        try:
            with open(self.log_path, "w", encoding="utf-8"):
                pass
        except OSError as exc:
            self.log_line("cannot write a log file: %s" % exc)
            return
        flags = 0x08000000 if os.name == "nt" else 0        # CREATE_NO_WINDOW
        try:
            self.proc = subprocess.Popen(self.controller_argv(self.log_path), cwd=BASE,
                                         creationflags=flags,
                                         stdin=subprocess.DEVNULL)
        except OSError as exc:
            self.log_line("could not start the overlay: %s" % exc)
            return
        self.log_line("overlay started (pid %d)" % self.proc.pid)
        self.status.configure(text="overlay running", foreground=theme.OK)
        threading.Thread(target=self._tail, args=(self.proc, self.log_path),
                         daemon=True).start()

    def _tail(self, proc, path):
        """Follow the controller's log file until it exits."""
        handle = None
        try:
            while True:
                if handle is None:
                    try:
                        handle = open(path, "r", encoding="utf-8", errors="replace")
                    except OSError:
                        if proc.poll() is not None:
                            return
                        time.sleep(0.1)
                        continue
                line = handle.readline()
                if line:
                    self.out.put(line)
                    continue
                if proc.poll() is not None:
                    rest = handle.read()
                    if rest:
                        self.out.put(rest)
                    return
                time.sleep(0.1)
        finally:
            if handle is not None:
                handle.close()
            self.out.put("\x00exited\x00")

    def stop_controller(self, quiet=False):
        if not self.running():
            if not quiet:
                self.log_line("not running")
            return
        proc = self.proc
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if not quiet:
            self.log_line("overlay stopped")
        self.status.configure(text="overlay stopped", foreground=theme.BAD)

    def _poll(self):
        drained = False
        try:
            while True:
                line = self.out.get_nowait()
                if line == "\x00exited\x00":
                    drained = True
                    if not self.running():
                        self.status.configure(text="overlay stopped", foreground=theme.BAD)
                    continue
                self.log_line(line)
        except queue.Empty:
            pass
        if self.running():
            self.status.configure(text="overlay running", foreground=theme.OK)
        del drained
        self.root.after(120, self._poll)

    # ------------------------------------------------------------------ lifecycle
    def _save_geometry(self):
        try:
            if self.root.state() == "normal":
                self.geometry = self.root.geometry()
        except tk.TclError:
            pass
        state = dict(self.state)
        if self.geometry:
            state["geometry"] = self.geometry
        else:
            state.pop("geometry", None)
        save_state(state)

    def on_close(self):
        self._save_geometry()
        # the overlay is a child of this window, so closing the window stops it rather than
        # leaving an invisible process holding the single-instance lock
        self.stop_controller(quiet=True)
        self.root.destroy()


def _bind_stdio(log_path=None):
    """Give the program somewhere to report.

    A --noconsole build has no console and PyInstaller sets sys.stdout to None to match it,
    at which point `print` is a silent no-op - so a working program looks like a dead one.
    Output is therefore bound explicitly:

      * the controller is handed a log FILE by the GUI (`--log`) and writes there. A file
        rather than a pipe is deliberate: a pipe depends on fd 1 having survived into a
        windowed build, which is a property of the build tool rather than of this program,
        and when it did not survive the child reported nothing at all.
      * everything else falls back to fd 1, so `--selftest` prints when run from a shell.
    """
    if log_path:
        try:
            stream = open(log_path, "w", encoding="utf-8", errors="replace", buffering=1)
        except OSError:
            stream = None
        if stream is not None:
            sys.stdout = stream
            sys.stderr = stream
            return
    for index, name in ((1, "stdout"), (2, "stderr")):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, os.fdopen(index, "w", encoding="utf-8",
                                             errors="replace", buffering=1))
            except OSError:
                pass


def run_controller():
    """The half that talks to the game - what overlays.py's __main__ does."""
    lock = core.claim_single_instance()
    if lock is None:
        return 3
    try:
        return core.main(FEATURES)
    finally:
        core.release_single_instance(lock)


def selftest():
    """Build the whole window without showing it, then throw it away: proves every setting
    got a widget and `gather()` can read them all back, without a human present."""
    root = tk.Tk()
    theme.apply_dark(root)
    app = App(root)
    print("warnings : %s" % (app.warnings or "none"))
    print("settings : %d controls" % len(app.vars))
    try:
        cfg = app.gather()
        print("gather   : ok, %d sections" % len(cfg))
        for name in sorted(cfg):
            keys = ", ".join("%s=%s" % (k, cfg[name][k]) for k in sorted(cfg[name]))
            print("  [%s] %s" % (name, keys))
    except ValueError as exc:
        print("gather   : FAILED - %s" % exc)
        root.destroy()
        return 1
    root.destroy()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Turn the Coromon overlay features on and off.")
    ap.add_argument("--controller", action="store_true",
                    help="run the overlay instead of the window (how a frozen build calls "
                         "itself)")
    ap.add_argument("--log", default=None,
                    help="where the controller writes its output; the GUI passes a file "
                         "because a windowed build has no usable stdout")
    ap.add_argument("--selftest", action="store_true",
                    help="build the window, check every setting reads back, then exit")
    args = ap.parse_args(argv)
    _bind_stdio(args.log)

    if args.controller:
        return run_controller()
    if args.selftest:
        return selftest()

    root = tk.Tk()
    theme.apply_dark(root)
    app = App(root)
    if app.cfg is not None:
        app.start_controller()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
