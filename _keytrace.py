#!/usr/bin/env python3
"""Scratch: install/show a passive key-event recorder. Deleted once answered."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scroll_fix import _bridge, _eval  # noqa: E402

INSTALL = r'''
if _G.__keytrace then return 'already tracing' end
local t = { events = {}, total = 0 }
_G.__keytrace = t
Runtime:addEventListener('key', function(e)
  if type(e) ~= 'table' then return end
  t.total = t.total + 1
  local s = tostring(e.keyName) .. '/' .. tostring(e.phase)
  -- isRepeat is reported explicitly: whether the field exists at all matters, because a
  -- missing field is what makes a down/up guard necessary in the first place
  s = s .. '  isRepeat=' .. tostring(e.isRepeat)
  if e.isDown ~= nil then s = s .. ' isDown=' .. tostring(e.isDown) end
  t.events[#t.events + 1] = s
  while #t.events > 80 do table.remove(t.events, 1) end
end)
return 'tracing'
'''

SHOW = r'''
local t = _G.__keytrace
if not t then return 'NOT_TRACING' end
local out = { string.format('events seen so far: %d   buffer: %d', t.total, #t.events) }
for i = 1, #t.events do
  out[#out + 1] = string.format('  %2d  %s', i, t.events[i])
end
return table.concat(out, '\n')
'''

if __name__ == "__main__":
    b = _bridge()
    if len(sys.argv) > 1 and sys.argv[1] == "show":
        print(_eval(b, SHOW, timeout=60.0))
    elif len(sys.argv) > 1 and sys.argv[1] == "clear":
        print(_eval(b, "if _G.__keytrace then _G.__keytrace.events = {} return 'cleared' end return 'not tracing'",
                    timeout=60.0))
    elif len(sys.argv) > 1 and sys.argv[1] == "off":
        print(_eval(b, "_G.__keytrace = nil return 'recorder removed'", timeout=60.0))
    else:
        print(_eval(b, INSTALL, timeout=60.0))
