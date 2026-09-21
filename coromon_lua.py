#!/usr/bin/env python3
"""
coromon_lua.py - run Lua code inside a running Coromon (Solar2D) process.

How it works
------------
Solar2D (Corona) embeds Lua 5.1 in `lua.dll`.  This tool attaches with Frida,
captures a `lua_State*` by hooking a few very common Lua C entry points, and
then executes arbitrary Lua inside the game with
`luaL_loadstring()` + `lua_pcall()`, returning the result as a string.

Usage
-----
    python coromon_lua.py --eval "return tostring(1+1)"
    python coromon_lua.py --file snippet.lua
    python coromon_lua.py            # interactive REPL

`Bridge` here still wants the game already running. A caller that has to cope with the game
not being up yet, or being closed and started again, uses `Bridge.try_attach()` (None when
the process cannot be attached to *yet* - either it is not there, or it is still starting up
or shutting down), watches `Bridge.gone` / `Bridge.wait()`, and treats a `GameGone`
raised out of `core.eval_` as "start over" rather than as an error to report. `lua.dll` is
resolved on a timer inside the script, so attaching during the game's own start-up works.
"""

import argparse
import ctypes
import sys
import threading
import time
from ctypes import wintypes

import frida

JS = r"""
/* ---- resolve lua.dll exports (Frida 17 API) ---- */
function resolveModule(name) {
    try { return Process.getModuleByName(name); } catch (e) {}
    try { return Module.load(name); } catch (e) {}
    return null;
}
var luaMod = null;         /* filled in by boot(), once the game has loaded lua.dll */
function exp(name) {
    if (luaMod) {
        try { return luaMod.getExportByName(name); } catch (e) {}
        try { return luaMod.findExportByName(name); } catch (e) {}
    }
    try { return Module.getGlobalExportByName(name); } catch (e) {}
    try { return Module.findGlobalExportByName(name); } catch (e) {}
    return null;
}

var p_gettop = null, p_settop = null, p_tolstring = null, p_loadstring = null, p_pcall = null;
var f_gettop = null, f_settop = null, f_tolstring = null, f_loadstring = null, f_pcall = null;

var mainL = null;          /* candidate lua_State captured from the game */
var L_counts = new Map();  /* count hits per state */
var pending = [];          /* queued requests */
var busy = false;
var hooked = false;

/* FAST MODE, and it is the whole reason this is not free.

   The onEnter below used to do, on EVERY hooked call: `L.toString()` (which allocates a string),
   a Map.get, a Map.set, then `mainL.toString()` and another Map.get. `lua_gettop` is called for
   essentially every Lua operation the VM performs, so that ran hundreds of thousands of times a
   second and cost a fixed slice of every frame - measured 2026-09-21 at ~2.6 ms per frame in the
   main menu (165 fps -> 115) with the tool attached and NOTHING installed, which is what
   identified it: no feature is involved, so the tax is in here.

   All of that work is for CAPTURE - picking the busiest lua_State - and it only has to be right
   once. So it runs for the first `countLimit` hits (enough evidence to see which state dominates,
   and a few milliseconds of real time) and then stops for the rest of the session. `total` keeps
   incrementing in fast mode, because perf_probe reads the hook-hit rate out of it.

   The alternative considered and rejected: detaching the Interceptor when idle. It cannot work -
   pump() runs Lua on the game's thread, and the only way to get a turn on that thread is for the
   game itself to call into a hooked function. The hook has to stay; it just does not have to
   count. */
var counted = 0;
var countLimit = 20000;
var fastMode = false;

/* Only `lua_gettop` is strictly needed to capture the state and run code; the
   rest are extra chances to catch the Lua thread, but they are extremely hot
   functions, so `--perf` style callers may want to trim this list. */
var hookTargets = __HOOK_TARGETS__;
var total = 0;

function ready() {
    return f_gettop !== null && f_settop !== null && f_tolstring !== null &&
           f_loadstring !== null && f_pcall !== null;
}

function resolveLua() {
    luaMod = resolveModule('lua.dll');
    if (!luaMod) return false;
    p_gettop     = exp('lua_gettop');
    p_settop     = exp('lua_settop');
    p_tolstring  = exp('lua_tolstring');
    p_loadstring = exp('luaL_loadstring');
    p_pcall      = exp('lua_pcall');
    if (!(p_gettop && p_settop && p_tolstring && p_loadstring && p_pcall)) return false;
    f_gettop     = new NativeFunction(p_gettop,     'int',     ['pointer']);
    f_settop     = new NativeFunction(p_settop,     'void',    ['pointer', 'int']);
    f_tolstring  = new NativeFunction(p_tolstring,  'pointer', ['pointer', 'int', 'pointer']);
    f_loadstring = new NativeFunction(p_loadstring, 'int',     ['pointer', 'pointer']);
    f_pcall      = new NativeFunction(p_pcall,      'int',     ['pointer', 'int', 'int', 'int']);
    return true;
}

/* Hook entry points that every C->Lua transition uses; all take L as arg0. */
function installHooks() {
    hookTargets.forEach(function (name) {
        var a = exp(name);
        if (!a) return;
        try {
            Interceptor.attach(a, {
                onEnter: function (args) {
                    var L = args[0];
                    if (!L || L.isNull()) return;
                    total++;
                    if (fastMode) {
                        /* the steady state: no string work, no Map work, just the pump check */
                        if (pending.length && !busy) pump();
                        return;
                    }
                    var key = L.toString();
                    var c = (L_counts.get(key) || 0) + 1;
                    L_counts.set(key, c);
                    /* the busiest state is the game's main state */
                    if (mainL === null || c > (L_counts.get(mainL.toString()) || 0)) mainL = L;
                    if (pending.length && !busy && mainL) pump();
                    counted++;
                    /* capture is over once a state has clearly won; see the fast-mode note above */
                    if (mainL !== null && counted >= countLimit) {
                        fastMode = true;
                        L_counts.clear();
                        send({type: 'info', msg: 'hook fast mode after ' + counted +
                              ' hits, state=' + mainL.toString()});
                    }
                }
            });
        } catch (e) { send({type: 'info', msg: 'hook fail ' + name + ': ' + e}); }
    });
    hooked = true;
}

/* lua.dll is not necessarily loaded by the time we attach - the game may still be starting
   up - so look again every so often rather than fail the attach on the first look. The
   `ready` message is what tells the other side the hooks are actually in. */
function boot() {
    if (ready()) return;
    if (!resolveLua()) { setTimeout(boot, 200); return; }
    installHooks();
    send({type: 'ready', msg: 'lua.dll=' + luaMod + ' gettop=' + p_gettop +
          ' loadstring=' + p_loadstring + ' pcall=' + p_pcall + ' tolstring=' + p_tolstring,
          gettop: p_gettop.toString(), loadstring: p_loadstring.toString(),
          pcall: p_pcall.toString(), tolstring: p_tolstring.toString()});
}
boot();

function readLuaString(L, idx) {
    try {
        var lenp = Memory.alloc(8);
        var sp = f_tolstring(L, idx, lenp);
        if (sp.isNull()) return null;
        var len = lenp.readU32();
        if (len === 0 || len > 8 * 1024 * 1024) return null;
        return sp.readUtf8String(len);
    } catch (e) { return null; }
}

function runLua(L, code) {
    var n = f_gettop(L);
    var out = null, err = null;
    try {
        var buf = Memory.allocUtf8String(code);
        var rc = f_loadstring(L, buf);
        if (rc !== 0) {
            err = 'luaL_loadstring failed: ' + readLuaString(L, -1);
        } else {
            var pr = f_pcall(L, 0, 1, 0);
            if (pr !== 0) err = 'lua error: ' + readLuaString(L, -1);
            else out = readLuaString(L, -1);
        }
    } catch (e) {
        err = 'native exception: ' + e;
    }
    try { f_settop(L, n); } catch (e) {}
    return {out: out, err: err};
}

var wrapper_pre = "local __ok,__res = pcall(function()\n";
var wrapper_post = "\nend)\nif not __ok then return '!ERROR! '..tostring(__res) end\nif __res == nil then return '!NIL!' end\nreturn tostring(__res)\n";

function pump() {
    busy = true;
    var req = pending.shift();
    var L = mainL;
    try {
        var r = runLua(L, wrapper_pre + req.code + wrapper_post);
        send({type: 'result', id: req.id, out: r.out, err: r.err, state: L.toString()});
    } catch (e) {
        send({type: 'result', id: req.id, out: null, err: '' + e, state: L ? L.toString() : null});
    }
    busy = false;
}

rpc.exports = {
    eval: function (id, code) {
        pending.push({id: id, code: code});
        /* RECAPTURE ON DEMAND. Fast mode stops updating mainL, so it gets re-verified here - at the
           one moment it is about to be used - instead of being trusted from startup for the whole
           session. mainL is deliberately NOT cleared: pump() can run on the very next hit, and the
           counting can only move it to a state that has been measurably busier. A few milliseconds
           of counting per request, and nothing between requests. */
        fastMode = false;
        counted = 0;
        /* give the game a nudge: if it is idle the hook may not fire for a while */
        return true;
    },
    status: function () {
        return {state: mainL ? mainL.toString() : null, hooks: total, queued: pending.length,
                ready: ready(), hooked: hooked, fast: fastMode, counted: counted};
    }
};
"""


DEFAULT_HOOKS = [
    "lua_gettop",
    "lua_pushnumber",
    "lua_pushstring",
    "lua_pushvalue",
    "lua_getfield",
    "lua_settop",
    "lua_type",
    "lua_pcall",
]
MINIMAL_HOOKS = ["lua_gettop"]

# Attach failures that mean "not yet" rather than "never". A game that comes and goes gets
# attached to over and over, and a process that is only just starting up - or already on its
# way out - is the normal case, not an error. Measured on this game: re-attaching right after
# it closed raises
#     frida.NotSupportedError: unexpected error allocating memory in target process
#                              (VirtualAllocEx returned 0x00000005)
# and the same refusal has also come out as a frida.TransportError, depending on which call
# trips over it. 0x5 is ACCESS_DENIED - the dying process is still listed, so the name lookup
# finds it, but it will not let the injector allocate inside it any more. Retrying a moment
# later picks up the process that replaced it. Frida 17 raises all of these straight off
# Exception with no common Frida base class, so they have to be named: the two spellings above
# are one condition, and both are named for that reason.
RETRYABLE_ATTACH_ERRORS = (
    frida.ProcessNotFoundError,
    frida.NotSupportedError,
    frida.TransportError,
    frida.PermissionDeniedError,
    frida.ProcessNotRespondingError,
)


class NotReadyYet(Exception):
    """The game is running but not up yet. See window_up(): raised INSTEAD of attaching."""


# Not-yet-ready is a reason to WAIT, not to fail: the same handling the Frida attach errors above
# get, and for the same reason. Named separately from them because it is not a Frida error at all.
RETRYABLE_ATTACH_ERRORS = RETRYABLE_ATTACH_ERRORS + (NotReadyYet,)


# --- is the game up yet? ----------------------------------------------------------------
#
# DO NOT ATTACH BEFORE THE GAME HAS A WINDOW THAT ANSWERS.
#
# Frida suspends the target's threads to install its hooks, and a process that is still
# initialising - loader lock held, DLLs part way through coming up - can be wedged by that.
# Measured: the game starts to a black screen and never runs a single line of Lua, while our side
# sits at "hooked lua.dll, waiting for the game to run some Lua" forever, because Lua never gets to
# run. Retrying the attach does not help; not doing it too early does. The window being up and
# answering is the earliest reliable sign that the process is far enough along.
#
# This is deliberately read through Win32 rather than Frida: it has to happen BEFORE the attach,
# and Frida cannot be asked about a process without attaching to it.

_user32 = ctypes.windll.user32 if sys.platform == "win32" else None

_SMTO_BLOCK = 0x0001
_SMTO_ABORTIFHUNG = 0x0002
_WM_NULL = 0x0000


def _top_level_windows():
    """[(hwnd, pid, title, visible)] for this session's top-level windows."""
    out = []
    if _user32 is None:
        return out
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        n = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        out.append((hwnd, pid.value, buf.value, bool(_user32.IsWindowVisible(hwnd))))
        return True

    _user32.EnumWindows(proc(cb), 0)
    return out


def _window_responds(hwnd):
    """True if the window answers WM_NULL within a second. A wedged window does not."""
    res = wintypes.DWORD()
    ok = _user32.SendMessageTimeoutW(
        hwnd, _WM_NULL, 0, 0, _SMTO_ABORTIFHUNG | _SMTO_BLOCK, 1000, ctypes.byref(res)
    )
    return bool(ok)


def pids_for(name):
    """Pids whose image name matches, without attaching to anything."""
    try:
        return [
            p.pid
            for p in frida.get_local_device().enumerate_processes()
            if (p.name or "").lower() == name.lower()
        ]
    except Exception:
        return []


def window_up(target):
    """(ok, why). True when `target` has a visible, answering top-level window.

    Anything that is not a reason to wait counts as ok: a platform without Win32, or a target that
    is not an image name at all (a pid, a path), so this can never become a reason a tool refuses to
    work.
    """
    if _user32 is None or not isinstance(target, str):
        return True, "no window check to make"
    pids = pids_for(target)
    if not pids:
        return False, "%s is not running" % target
    windows = [w for w in _top_level_windows() if w[1] in pids]
    if not windows:
        return False, "%s is running, but it has no window yet" % target
    visible = [w for w in windows if w[3]]
    if not visible:
        return False, "%s is running, but its window is not shown yet" % target
    if any(_window_responds(w[0]) for w in visible):
        return True, "window is up and answering"
    return False, (
        "%s is running, and its window is not answering - still starting, or already hung"
        % target
    )


def build_js(hooks=None):
    """BRIDGE JS with the hook list substituted in."""
    import json as _json

    return JS.replace("__HOOK_TARGETS__", _json.dumps(list(hooks or DEFAULT_HOOKS)))


class GameGone(Exception):
    """The game closed (or Frida lost it) while we were talking to it.

    Its own exception rather than an error string because the two want opposite reactions:
    a Lua error is reported, a game that has gone away means start over.
    """


class Bridge:
    def __init__(self, target="coromon.exe", hooks=None):
        up, why = window_up(target)
        if not up:
            raise NotReadyYet(why)
        self.target = target
        self._results = {}
        self._events = []
        self._ready = None
        self._ev = threading.Event()
        self._gone = threading.Event()
        self.session = None
        self.script = None
        try:
            self.session = frida.attach(target)
            # The session outlives the script, and it is what tells us the game went away -
            # which matters as much as the Lua answers do, because the game gets closed and
            # started again and the caller has to notice.
            self.session.on("detached", self._on_detached)
            self.script = self.session.create_script(build_js(hooks))
            self.script.on("message", self._on_message)
            self.script.load()
        except Exception:
            # Half-built. Let go of the session before letting the error out, so a caller
            # that retries in a loop (see try_attach) cannot pile up stale attachments - the
            # process is on its way out and the next attempt needs it to be gone.
            self.detach()
            raise

    @classmethod
    def try_attach(cls, target="coromon.exe", hooks=None, on_retry=None):
        """Attach, or None if the game cannot be attached to yet.

        None covers both "not running" and "there, but not ready" - a game that has only
        just been launched refuses the injector while it starts, and one that is closing
        refuses it too. `on_retry` is handed the underlying exception so the caller can say
        why it is waiting, without having to know which Frida errors are worth waiting for.
        """
        try:
            return cls(target, hooks=hooks)
        except RETRYABLE_ATTACH_ERRORS as exc:
            if on_retry is not None:
                on_retry(exc)
            return None

    def _on_detached(self, *args):
        self._gone.set()
        self._ev.set()

    def _on_message(self, message, data):
        if message["type"] == "send":
            payload = message["payload"]
            kind = payload.get("type")
            if kind == "result":
                self._results[payload["id"]] = payload
                self._ev.set()
            elif kind == "ready":
                self._ready = payload
                self._ev.set()
            else:
                self._events.append(payload)
        else:
            self._events.append(message)

    @property
    def gone(self):
        """True once the game has closed (or Frida lost it)."""
        return self._gone.is_set()

    @property
    def ready(self):
        """True once the script has found lua.dll and hooked it - which can be a moment
        after the attach, because the game may still be starting up when we get there."""
        return bool(self._ready)

    @property
    def _session_detached(self):
        """Whether Frida itself considers the session dead.

        frida exposes `is_detached` as a plain attribute in 17.x and as a method in older
        versions, and a wrong guess here is not harmless: calling a bool raises TypeError,
        and if that lands in a bare except the check silently never fires - which is a
        watcher that sits there forever instead of noticing the game closed. So accept both.
        """
        session = self.session
        if session is None:
            return True
        flag = getattr(session, "is_detached", None)
        if flag is None:
            return False
        try:
            return bool(flag() if callable(flag) else flag)
        except Exception:
            return False

    def wait(self, timeout=None):
        """Block until the game goes away. True if it did, False if it is still there.

        The detached signal is what normally says so, and the session's own flag is checked
        as well: a watcher sitting here forever because one notification went missing is the
        one failure this cannot afford.
        """
        if self.session is None:
            return True  # never attached properly - nothing to wait for
        deadline = None if timeout is None else time.time() + timeout
        while True:
            if self._gone.is_set():
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            self._gone.wait(0.5)
            if self._session_detached:
                self._on_detached()
                return True

    def _closed(self):
        return {"out": None, "err": "the game closed", "state": None, "closed": True}

    def eval(self, code, timeout=10.0):
        if self.gone:
            return self._closed()
        rid = str(time.time())
        try:
            self.script.exports_sync.eval(rid, code)
        except Exception as exc:
            # Whatever Frida's error says, if the session died the game went away, and the
            # caller is told that rather than handed a decode of somebody else's problem.
            if self.gone:
                return self._closed()
            return {"out": None, "err": str(exc), "state": None}
        deadline = time.time() + timeout
        while time.time() < deadline:
            if rid in self._results:
                return self._results.pop(rid)
            self._ev.wait(0.2)
            self._ev.clear()
            if self.gone:
                self._results.pop(rid, None)
                return self._closed()
        return {
            "out": None,
            "err": "timeout waiting for Lua execution (is the game idle / paused?)",
            "state": None,
        }

    def status(self):
        """The script's own state, or None once the game has gone (or the script is dead)."""
        if self.gone:
            return None
        try:
            return self.script.exports_sync.status() or None
        except Exception:
            return None

    def detach(self):
        """Let go of the session, whatever state it is in. Safe to call twice, and safe on a
        bridge that never finished attaching - `_gone` is set defensively because a detach can
        be reached from the failure path inside __init__, where nothing can be assumed."""
        session, self.session, self.script = self.session, None, None
        gone = getattr(self, "_gone", None)  # an eval after this is "the game is gone"
        if gone is not None:
            gone.set()
        if session is not None:
            try:
                session.detach()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--process", default="coromon.exe")
    ap.add_argument("--eval", dest="code")
    ap.add_argument("--file")
    args = ap.parse_args()

    b = Bridge(args.process)
    time.sleep(0.2)
    st = b.status() or {}
    print(
        f"[bridge] state={st.get('state')} hook_hits={st.get('hooks')}", file=sys.stderr
    )
    if st.get("state") is None:
        print("[bridge] no lua_State captured yet - waiting...", file=sys.stderr)

    if args.code:
        r = b.eval(args.code)
        print(r["out"] if r["out"] is not None else ("ERROR: " + str(r["err"])))
    elif args.file:
        r = b.eval(open(args.file).read())
        print(r["out"] if r["out"] is not None else ("ERROR: " + str(r["err"])))
    else:
        while True:
            try:
                code = input("lua> ")
            except EOFError:
                break
            if not code.strip():
                continue
            r = b.eval(code)
            print(r["out"] if r["out"] is not None else ("ERROR: " + str(r["err"])))
    b.detach()


if __name__ == "__main__":
    main()
