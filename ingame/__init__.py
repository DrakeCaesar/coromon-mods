#!/usr/bin/env python3
"""
ingame - the live in-game helpers, one module per feature.

`core.py` knows how to talk to the running game and how to compose a single Lua chunk out
of the features; it knows nothing about what the features *are*. Each feature module
exposes the same small surface, which is all core asks of it:

    NAME                    registry key, and the name of its table in overlays.toml
    SETTINGS                [(key, default, comment)] - its settings, and the file's schema
    lua(cfg)                its query code, always part of every composed chunk
    section(cfg)            the block that registers it, or "" when it is switched off
    summary(cfg)            Lua expression -> the line the installer prints for it, or nil
    status(cfg)             Lua expression -> a line for the status report, or nil
    report(cfg)             Lua expression -> a line for the report, or nil
    ACTIONS                 optional: setting name -> one-shot Lua chunk

`cfg` is that feature's section of overlays.toml with its defaults filled in; `enabled` is
a setting like any other, and it is what `section`, the installer's summary and the ACTIONS
look at. The summary/status/report expressions are evaluated *inside* the composed chunk, so
they can call whatever helpers the module defined in its own `lua()`.

Adding a feature is one module plus one line in FEATURES below. Its settings document
themselves, because overlays.toml is generated from SETTINGS.
"""

from . import (
    config,
    cooldowns,
    core,
    dialog,
    fog,
    gold,
    guard,
    items,
    potential,
    reload,
    sprint,
    squad,
    steptimer,
    zoom,
)

# Order is only the order their lines appear in the installer's output - except that `guard` is
# first on purpose: it wraps groupHelper.setObjectContainer, and installing it before the others
# means the wrap is in place before any of them touch the UI.
FEATURES = [
    guard,
    potential,
    items,
    squad,
    steptimer,
    zoom,
    sprint,
    reload,
    gold,
    dialog,
    cooldowns,
    fog,
]

__all__ = [
    "config",
    "cooldowns",
    "core",
    "dialog",
    "fog",
    "gold",
    "guard",
    "items",
    "potential",
    "reload",
    "sprint",
    "squad",
    "steptimer",
    "zoom",
    "FEATURES",
]
