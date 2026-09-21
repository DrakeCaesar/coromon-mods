"""What the window remembers between runs, and the window's own geometry.

QSettings rather than a JSON file of our own: Qt already knows where per-user settings
belong, and it stores `saveGeometry()` as one opaque QByteArray that `restoreGeometry()`
consumes - which carries the position, the size AND whether the window was maximized or
fullscreen. The Tk version had to save "WxH+X+Y" and then guess, and a maximized window
reported the screen size as its own, which is the state it could not restore.

The user's own choices (which areas are ticked, the filters, the sort orders) are one JSON
blob under a single key, because QSettings stores strings on Windows and reading a list
back out of the registry would need a decode step per key.
"""

import json

from PySide6.QtCore import QByteArray, QSettings

from .paths import APP_NAME, LEGACY_STATE

ORG = "Coromon"
GEOMETRY_KEY = "window/geometry"
PREFS_KEY = "prefs"
MIGRATED_KEY = "state/migrated"

# The keys of the old JSON file that are still meaningful, so the tick list and the filters
# built up under the Tk version survive the move. Its "geometry" and "zoomed" are dropped:
# QSettings stores the window as one opaque blob that already carries both, and keeping a
# second copy of the position is how the two go out of step. "tab" is kept - the three tabs
# are in the same order as before, so the saved index still means what it meant.
PREF_KEYS = ("available", "level", "min_share", "only_xp", "on_top", "tab",
             "skill_search", "skill_type", "skill_sort", "skill_desc")


def settings():
    return QSettings(ORG, APP_NAME)


def _legacy_prefs():
    """The preferences the Tk version saved, or {} if there is nothing to migrate."""
    try:
        with open(LEGACY_STATE, encoding="utf-8") as fh:
            saved = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(saved, dict):
        return {}
    return {k: v for k, v in saved.items() if k in PREF_KEYS}


def load_prefs():
    """Every saved preference, importing the Tk version's file the first time.

    The import runs ONCE - it is stamped in the settings - so a later run cannot resurrect a
    stale tick list from the old file, and the old file is never written to.
    """
    store = settings()
    raw = store.value(PREFS_KEY, "")
    prefs = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            prefs = parsed
    if not store.value(MIGRATED_KEY, False):
        for key, value in _legacy_prefs().items():
            prefs.setdefault(key, value)
        store.setValue(MIGRATED_KEY, True)
        save_prefs(prefs)
    return prefs


def save_prefs(prefs):
    settings().setValue(PREFS_KEY, json.dumps(prefs))


def save_geometry(window):
    """Store the window's position, size and maximized state as one value."""
    settings().setValue(GEOMETRY_KEY, window.saveGeometry())
def restore_geometry(window, width, height):
    """Put a window back where it was, maximized or fullscreen included."""
    window.resize(width, height)
    raw = settings().value(GEOMETRY_KEY)
    if isinstance(raw, QByteArray) and not raw.isEmpty():
        window.restoreGeometry(raw)
        # restoreGeometry brings back the size and position; the window state is asked for
        # separately, and asking for it is what makes a fullscreen or maximized window come
        # back the way it was rather than merely sized like one.
        if window.isFullScreen():
            window.showFullScreen()
        elif window.isMaximized():
            window.showMaximized()


class Prefs:
    """The saved preferences as a mapping that writes itself back on every change.

    Written on every change rather than at close, because every one of these values IS a user
    decision - a tick, a filter, a sort order - and the blob is under a kilobyte, so the cost
    is nothing against losing the tick list to a crash.

    One instance is shared by the whole window: two of them would each write their own idea of
    the whole dict and the second would erase the first's changes.
    """

    def __init__(self, values=None):
        self.values = dict(values or {})

    @classmethod
    def load(cls):
        return cls(load_prefs())

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        if self.values.get(key) != value:
            self.values[key] = value
            save_prefs(self.values)
