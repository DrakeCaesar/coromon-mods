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
the process is not there), watches `Bridge.gone` / `Bridge.wait()`, and treats a `GameGone`
raised out of `core.eval_` as "start over" rather than as an error to report. `lua.dll` is
resolved on a timer inside the script, so attaching during the game's own start-up works.
"""
import argparse
import sys
import threading
import time

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
                    var key = L.toString();
                    var c = (L_counts.get(key) || 0) + 1;
                    L_counts.set(key, c);
                    /* the busiest state is the game's main state */
                    if (mainL === null || c > (L_counts.get(mainL.toString()) || 0)) mainL = L;
                    if (pending.length && !busy && mainL) pump();
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
        /* give the game a nudge: if it is idle the hook may not fire for a while */
        return true;
    },
    status: function () {
        return {state: mainL ? mainL.toString() : null, hooks: total, queued: pending.length,
                ready: ready(), hooked: hooked};
    }
};
"""


DEFAULT_HOOKS = ["lua_gettop", "lua_pushnumber", "lua_pushstring", "lua_pushvalue",
                 "lua_getfield", "lua_settop", "lua_type", "lua_pcall"]
MINIMAL_HOOKS = ["lua_gettop"]


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
        self.target = target
        self._results = {}
        self._events = []
        self._ready = None
        self._ev = threading.Event()
        self._gone = threading.Event()
        self.session = frida.attach(target)
        # The session outlives the script, and it is what tells us the game went away -
        # which matters as much as the Lua answers do, because the game gets closed and
        # started again and the caller has to notice.
        self.session.on("detached", self._on_detached)
        self.script = self.session.create_script(build_js(hooks))
        self.script.on("message", self._on_message)
        self.script.load()

    @classmethod
    def try_attach(cls, target="coromon.exe", hooks=None):
        """Attach, or None if the process is not running (yet)."""
        try:
            return cls(target, hooks=hooks)
        except frida.ProcessNotFoundError:
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

    def wait(self, timeout=None):
        """Block until the game goes away. True if it did, False if it is still there.

        The detached signal is what normally says so, and the session's own flag is checked
        as well: a watcher sitting here forever because one notification went missing is the
        one failure this cannot afford.
        """
        deadline = None if timeout is None else time.time() + timeout
        while True:
            if self._gone.is_set():
                return True
            if deadline is not None and time.time() >= deadline:
                return False
            self._gone.wait(0.5)
            try:
                if self.session.is_detached():
                    self._on_detached()
                    return True
            except Exception:
                pass

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
        return {"out": None, "err": "timeout waiting for Lua execution (is the game idle / paused?)",
                "state": None}

    def status(self):
        """The script's own state, or None once the game has gone (or the script is dead)."""
        if self.gone:
            return None
        try:
            return self.script.exports_sync.status() or None
        except Exception:
            return None

    def detach(self):
        try:
            self.session.detach()
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
    print(f"[bridge] state={st.get('state')} hook_hits={st.get('hooks')}", file=sys.stderr)
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
