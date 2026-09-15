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
var luaMod = resolveModule('lua.dll');
function exp(name) {
    if (luaMod) {
        try { return luaMod.getExportByName(name); } catch (e) {}
        try { return luaMod.findExportByName(name); } catch (e) {}
    }
    try { return Module.getGlobalExportByName(name); } catch (e) {}
    try { return Module.findGlobalExportByName(name); } catch (e) {}
    return null;
}

var p_gettop     = exp('lua_gettop');
var p_settop     = exp('lua_settop');
var p_tolstring  = exp('lua_tolstring');
var p_loadstring = exp('luaL_loadstring');
var p_pcall      = exp('lua_pcall');

send({type: 'info', msg: 'lua.dll=' + luaMod + ' gettop=' + p_gettop + ' loadstring=' + p_loadstring +
      ' pcall=' + p_pcall + ' tolstring=' + p_tolstring});

var f_gettop     = p_gettop     ? new NativeFunction(p_gettop,     'int',    ['pointer']) : null;
var f_settop     = p_settop     ? new NativeFunction(p_settop,     'void',   ['pointer', 'int']) : null;
var f_tolstring  = p_tolstring  ? new NativeFunction(p_tolstring,  'pointer', ['pointer', 'int', 'pointer']) : null;
var f_loadstring = p_loadstring ? new NativeFunction(p_loadstring, 'int',    ['pointer', 'pointer']) : null;
var f_pcall      = p_pcall      ? new NativeFunction(p_pcall,      'int',    ['pointer', 'int', 'int', 'int']) : null;

var mainL = null;          /* candidate lua_State captured from the game */
var L_counts = new Map();  /* count hits per state */
var pending = [];          /* queued requests */
var busy = false;

/* Hook entry points that every C->Lua transition uses; all take L as arg0. */
/* Only `lua_gettop` is strictly needed to capture the state and run code; the
   rest are extra chances to catch the Lua thread, but they are extremely hot
   functions, so `--perf` style callers may want to trim this list. */
var hookTargets = __HOOK_TARGETS__;
var total = 0;
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
        return {state: mainL ? mainL.toString() : null, hooks: total, queued: pending.length};
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


class Bridge:
    def __init__(self, target="coromon.exe", hooks=None):
        self.session = frida.attach(target)
        self.script = self.session.create_script(build_js(hooks))
        self.script.on("message", self._on_message)
        self._results = {}
        self._events = []
        self._ev = threading.Event()
        self.script.load()

    def _on_message(self, message, data):
        if message["type"] == "send":
            payload = message["payload"]
            if payload.get("type") == "result":
                self._results[payload["id"]] = payload
                self._ev.set()
            else:
                self._events.append(payload)
        else:
            self._events.append(message)

    def eval(self, code, timeout=10.0):
        rid = str(time.time())
        self.script.exports_sync.eval(rid, code)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if rid in self._results:
                return self._results.pop(rid)
            self._ev.wait(0.2)
            self._ev.clear()
        return {"out": None, "err": "timeout waiting for Lua execution (is the game idle / paused?)",
                "state": None}

    def status(self):
        return self.script.exports_sync.status()

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
    st = b.status()
    print(f"[bridge] state={st['state']} hook_hits={st['hooks']}", file=sys.stderr)
    if st["state"] is None:
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
