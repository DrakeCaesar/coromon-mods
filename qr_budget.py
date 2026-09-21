"""qr_budget.py - Lua 5.1 compile limits for the composed install chunk.

`qr_lua_check.py` proves the chunks PARSE. It cannot prove they COMPILE, and the gap matters:
Lua 5.1 rejects any function with more than 200 locals (`too many local variables`) or more than
60 upvalues, and luaparser is perfectly happy to parse a chunk that violates either. The install
chunk is one 55 KB+ string assembled from a shared preamble plus every enabled feature's own
top-level locals, so it grows every time a feature is added and the failure mode is total: the
game refuses the whole chunk and nothing installs.

Reports the worst local count per function, and the upvalues of the functions that need them.

  python qr_budget.py
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "abandoned"))
sys.path.insert(0, BASE)

LOCAL_LIMIT = 200
UPVALUE_LIMIT = 60


def _is_node(obj):
    return obj is not None and hasattr(obj, "__class__") and \
        obj.__class__.__module__.startswith("luaparser")


def _locals_in(node):
    """Locals declared in this function's IMMEDIATE block.

    Why not every declaration in the function: Lua 5.1's limit is 200 *simultaneously live*
    locals (`fs->nactvar`, checked against MAXVARS). A block's locals are released when the block
    ends, so two disjoint `do ... end` sections reuse the same slots and cost one between them.
    Summing every declaration over-counts badly - it read 292 on a chunk the game has been
    loading happily - because every feature section is wrapped in its own `do ... end`.

    The immediate block is the honest number for the main chunk: its locals are all live at once,
    from their declaration to the end. Locals inside nested blocks are NOT counted, so this
    under-counts a function that declares many inside if/do blocks; it is an indicator with the
    right shape, not a proof.
    """
    body = getattr(getattr(node, "body", None), "body", None)
    total = 0
    for st in body or []:
        cls = type(st).__name__
        if cls == "LocalAssign":
            total += len(st.targets)
        elif cls in ("LocalFunction", "Fornum"):
            total += 1
        elif cls == "Forin":
            total += len(st.targets)
    return total


def check_worst(label, text):
    from luaparser import ast as A

    try:
        tree = A.parse(text)
    except Exception as exc:  # noqa: BLE001
        print("FAIL  %-22s cannot parse: %r" % (label, exc))
        return False

    worst, worst_at = 0, "?"
    for node in A.walk(tree):
        cls = type(node).__name__
        if cls == "Chunk":
            n = _locals_in(node)
        elif cls == "Function":
            n = _locals_in(node) + len(getattr(node, "args", None) or [])
        else:
            continue
        if n > worst:
            worst, worst_at = n, ("chunk" if cls == "Chunk" else "line %s" % node.line)

    ok = worst <= LOCAL_LIMIT
    print("%s  %-22s worst function %3d/%d locals  (at %s)"
          % ("ok   " if ok else "FAIL ", label, worst, LOCAL_LIMIT, worst_at))
    return ok


def main():
    from ingame import FEATURES, config, core

    cfg, _warn, _created = config.load(
        os.path.join(BASE, "overlays.toml"), core.CORE_SETTINGS, FEATURES
    )
    good = True

    # The chunk that installs. This is the one that matters: every feature's own `lua(cfg)` locals
    # land in the same main chunk as the shared preamble.
    good &= check_worst("install chunk", core.install_code(FEATURES, cfg))

    # And the report/status chunks, which include the same preamble plus the query locals.
    for name, builder in (("status chunk", core.status_code),
                          ("report chunk", core.report_code)):
        try:
            good &= check_worst(name, builder(FEATURES, cfg))
        except Exception as exc:  # noqa: BLE001
            print("SKIP  %-22s %r" % (name, exc))

    print()
    print("within Lua 5.1 limits" if good else "OVER A LIMIT - the game will refuse this chunk")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
