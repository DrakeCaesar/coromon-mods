#!/usr/bin/env python3
"""
build_exe.py - build the single-file CoromonOverlays.exe.

One command, because the interesting part is why each option is there:

    python build_exe.py

Produces `dist/CoromonOverlays.exe`. Nothing is copied into the game directory: the exe is
run from wherever it is put, it attaches to `coromon.exe` by name, and it writes its settings
beside itself (see `ingame/core.py:_data_dir` - a one-file build unpacks into a temp
directory that is deleted on exit, so the settings must not live there).

The options, and what goes wrong without them:

  --onefile     the whole point: one file to hand somebody. It self-extracts to a temp
                directory on each run, which costs about a second of start-up.
  --noconsole   no console window behind the GUI. This is also what makes `_bind_stdio` in
                overlays_gui.py necessary: PyInstaller sets sys.stdout to None to match the
                missing console, so the controller half would report nothing.
  --collect-all frida
                frida is not a pure-Python package. It ships `_frida.pyd` plus the frida-core
                DLLs that ride along with it, and static analysis alone finds neither - the
                build succeeds and then fails at `import frida` with a missing DLL. This is
                the single most likely thing to break a rebuild.
  --paths .     so `ingame/`, `coromon_lua.py` and `gui_theme.py` - all imported by relative
                path from the entry script - are found during analysis.

`overlays.toml` is deliberately NOT bundled. The program reads and writes the one beside the
.exe, and writes a fresh one from the schema if it is missing, so a copy inside the archive
would never be consulted - it would only be a second file to get out of step.

The entry point is `overlays_gui.py` and it is both halves of the program: with no arguments
it is the window, and with `--controller` it is the overlay itself. A frozen build has no
`overlays.py` on disk to spawn, so the GUI starts the exe again with that flag.
"""

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = "overlays_gui.py"
NAME = "CoromonOverlays"


def main():
    os.chdir(HERE)

    if not os.path.exists(ENTRY):
        print("run this from the tools directory - %s is not here" % ENTRY)
        return 2

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Install it with:\n"
              "    python -m pip install pyinstaller")
        return 2

    # A stale build directory is the classic source of "it still does the old thing".
    for stale in ("build", "dist"):
        if os.path.isdir(stale):
            shutil.rmtree(stale, ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name", NAME,
        "--collect-all", "frida",
        "--paths", HERE,
    ]
    cmd.append(ENTRY)

    print(" ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("\nbuild failed (exit %d)" % result.returncode)
        return result.returncode

    exe = os.path.join("dist", NAME + ".exe")
    if not os.path.exists(exe):
        print("\nPyInstaller reported success but %s is not there" % exe)
        return 1
    size = os.path.getsize(exe) / (1024.0 * 1024.0)
    print("\nbuilt %s  (%.1f MB)" % (exe, size))
    print("\ncheck it with:")
    print("    %s --selftest      (builds the window headlessly and reads every setting)" % exe)
    print("    %s --controller    (runs the overlay; Ctrl+C to stop)" % exe)
    print("\nHand somebody the single .exe. It attaches to coromon.exe and keeps its")
    print("overlays.toml beside itself, so it can sit in any folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
