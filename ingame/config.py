#!/usr/bin/env python3
"""
config.py - the settings file: its schema, its defaults, and reading it back.

There are no command line options anywhere in these tools. Every switch lives in one
`overlays.toml` beside `overlays.py`, and a run makes the game match it: the installer tears
down whatever was installed last time and installs what the file now says. Setting a
feature's `enabled` to false and running again is how you remove it.

The schema is declared by the pieces themselves - `core.SETTINGS` for the top level, and each
feature module's `SETTINGS` - so the file is *generated* from it, and a feature's settings are
documented in the same place they are defined. If `overlays.toml` is missing it is written
from that schema, comments and all.

A setting is `(key, default, comment)`. A value in the file whose type does not match its
default is reported and the default used in its place, so a typo cannot take a feature down.
"""

import sys
import textwrap

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - only on Python < 3.11
    raise SystemExit(
        "overlays.py needs Python 3.11 or newer for tomllib (running %s)"
        % sys.version.split()[0]
    )

FILENAME = "overlays.toml"

HEADER = """\
# overlays.toml - settings for overlays.py
#
# Edit this file, then run:   python overlays.py
#
# There are no command line options; everything comes from here. A run makes the game match
# this file - it tears down whatever was installed last time and installs what is enabled
# below - so setting `enabled` to false and running again is how you remove a feature.
#
# overlays.py waits for the game if it is not running yet, and re-attaches by itself when
# the game is closed and started again - it re-reads this file for each new session, so an
# edit made while the game is down is picked up by the next launch.
"""


class ConfigError(Exception):
    pass


def _fmt(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return '"%s"' % value
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    raise ConfigError("no way to write a default of type %s" % type(value).__name__)


def _norm(entry):
    """A setting is (key, default, comment), optionally with a fourth element: the values it
    is allowed to take."""
    return entry[0], entry[1], entry[2], (entry[3] if len(entry) > 3 else None)


def _wrap(comment, width=96):
    """A comment as `#` lines.

    The schema holds each comment as one string, so the line breaks have to be put in here.
    Without this a regenerated file is a wall of very long lines - the comments in the file
    that ships were wrapped by hand, and a write from the GUI should not make it worse.
    """
    return ["# " + line for line in textwrap.wrap(comment, width=width) or [""]]


def _setting_lines(settings, values):
    """The comment and the value for each setting, as file lines."""
    lines = []
    for entry in settings:
        key, default, comment, choices = _norm(entry)
        if choices:
            comment += "   (one of: %s)" % ", ".join(str(c) for c in choices)
        lines += _wrap(comment)
        lines.append("%s = %s" % (key, _fmt(values.get(key, default))))
    return lines


def _write(path, core_settings, features, cfg):
    """The whole file, from the schema, with `cfg`'s values."""
    lines = [HEADER.rstrip("\n"), ""]
    lines += _setting_lines(core_settings, cfg.get("core", {}))
    for f in features:
        lines.append("")
        lines.append("[%s]" % f.NAME)
        lines += _setting_lines(f.SETTINGS, cfg.get(f.NAME, {}))
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def render(path, core_settings, features):
    """Write the settings file out of the schema, comments and all, at their defaults."""
    return _write(path, core_settings, features, {})


def save(path, core_settings, features, cfg):
    """Write the file back out of the schema, but with the given values instead of the
    defaults - the write side of a GUI edit.

    The comments come from SETTINGS, exactly as in `render`, so a file rewritten this way
    keeps every explanation. Anything a person added by hand is not preserved, and comment
    wrapping is normalised: the file is generated, which is why it says so at the top.
    """
    return _write(path, core_settings, features, cfg)


def _resolve(section, settings, given):
    """Fill one section from the file, over the defaults, complaining about anything odd."""
    values, warnings = {}, []
    known = {_norm(entry)[0] for entry in settings}
    for entry in settings:
        key, default, comment, choices = _norm(entry)
        if key not in given:
            values[key] = default
            continue
        value = given[key]
        # A bare key name is accepted where the default is a list of them.
        if isinstance(default, list) and isinstance(value, str):
            value = [value]
        if type(value) is not type(default):
            warnings.append(
                "%s.%s: expected %s, found %s - using the default (%s)"
                % (
                    section,
                    key,
                    type(default).__name__,
                    type(value).__name__,
                    _fmt(default),
                )
            )
            value = default
        elif isinstance(value, list) and not all(isinstance(v, str) for v in value):
            warnings.append(
                "%s.%s: every entry should be a string - using the default"
                % (section, key)
            )
            value = default
        elif choices and value not in choices:
            warnings.append(
                "%s.%s: must be one of %s - using the default (%s)"
                % (section, key, ", ".join(str(c) for c in choices), _fmt(default))
            )
            value = default
        values[key] = value
    for key in given:
        if key not in known:
            warnings.append("%s.%s: not a setting - ignored" % (section, key))
    return values, warnings


def load(path, core_settings, features):
    """Read the file, writing it from the schema first if it is not there yet.

    Returns (config, warnings, created), where config is {'core': {...}, '<feature>': {...}}.
    """
    created = False
    import os

    if not os.path.exists(path):
        render(path, core_settings, features)
        created = True

    with open(path, "rb") as fh:
        try:
            raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError("%s is not valid TOML: %s" % (path, exc)) from None

    warnings = []
    top = {k: v for k, v in raw.items() if not isinstance(v, dict)}
    sections = {k: v for k, v in raw.items() if isinstance(v, dict)}

    cfg = {}
    cfg["core"], w = _resolve("<top level>", core_settings, top)
    warnings += w

    names = set()
    for f in features:
        names.add(f.NAME)
        cfg[f.NAME], w = _resolve(f.NAME, f.SETTINGS, sections.get(f.NAME, {}))
        warnings += w

    for name in sections:
        if name not in names:
            warnings.append("[%s]: not a known feature - ignored" % name)

    # THE MASTER SWITCH, applied last so it wins whatever the sections say. Attaching the tool
    # costs something on its own - one Interceptor on lua_gettop, which the VM calls for
    # essentially every Lua operation, and it stays attached for the whole session - while a
    # feature's cost only exists while that feature is installed. Turning everything off at once
    # here is what separates the two, and doing it here rather than by editing every section
    # means the per-feature flags keep their values, so switching back is one line either way.
    if cfg["core"].get("features_enabled") is False:
        for f in features:
            cfg[f.NAME]["enabled"] = False
        warnings.append(
            "features_enabled = false in the file: attached with NOTHING installed, on purpose"
        )

    return cfg, warnings, created
