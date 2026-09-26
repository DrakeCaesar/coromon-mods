-- READ-ONLY probe: wrap every installed feature's `update` with a stopwatch so the periodic cost is
-- attributed to the feature that causes it, instead of guessed from the periods.
--
-- It only changes OUR OWN functions (the ones _G.__hud.feats holds); nothing in the game is touched.
-- The host's `tick` calls `f.update` through the table, so a wrapper is seen exactly like the
-- original. It does NOT wrap the tick body itself: the tick's guard is `h.tickBody ~= tick`, and
-- replacing that field would make the tick return immediately - i.e. silently switch the whole tool
-- off.
-- `_G.__tt_remove()` puts every original back and takes the frame listener off. Run it when done:
-- each wrapper costs two `system.getTimer()` calls per update, which is not free while measuring fps.
_G.__tt_remove = function()
  local tt = _G.__tt
  if type(tt) ~= 'table' then return 'nothing to remove' end
  local h, back = tt.hud, 0
  if type(h) == 'table' and type(h.feats) == 'table' then
    for name, f in pairs(h.feats) do
      if tt.orig[name] then f.update = tt.orig[name] back = back + 1 end
    end
  end
  pcall(function() Runtime:removeEventListener('enterFrame', tt.ef) end)
  _G.__tt = nil
  return 'restored ' .. back .. ' update(s), listener removed'
end

if type(_G.__tt) == 'table' then
  return 'already installed - _G.__tt.report() for numbers, _G.__tt_remove() to stop'
end

local h = _G.__hud
if type(h) ~= 'table' or type(h.feats) ~= 'table' then return 'no hud installed' end

local tt = { stats = {}, orig = {}, t0 = system.getTimer(), wrapped = {}, hud = h }
_G.__tt = tt

for name, f in pairs(h.feats) do
  if type(f.update) == 'function' then
    local orig = f.update
    tt.orig[name] = orig
    local rec = { calls = 0, total = 0, max = 0, period = f.period or 0 }
    tt.stats[name] = rec
    f.update = function(...)
      local s = system.getTimer()
      local ok, err = pcall(orig, ...)
      local dt = system.getTimer() - s
      rec.calls = rec.calls + 1
      rec.total = rec.total + dt
      if dt > rec.max then rec.max = dt end
      if not ok then rec.err = tostring(err) end
      return ok
    end
    tt.wrapped[#tt.wrapped + 1] = name
  end
end

-- Frame timing as well, so a feature's worst tick can be read against the frame it landed in.
tt.frames, tt.fmin, tt.fmax, tt.fsum, tt.last = 0, nil, 0, 0, nil
tt.ef = function()
  local s = system.getTimer()
  if tt.last then
    local d = s - tt.last
    tt.frames = tt.frames + 1
    tt.fsum = tt.fsum + d
    if d > tt.fmax then tt.fmax, tt.fmax_at = d, s - tt.t0 end
    if not tt.fmin or d < tt.fmin then tt.fmin = d end
  end
  tt.last = s
end
Runtime:addEventListener('enterFrame', tt.ef)

tt.report = function()
  local out = {}
  local secs = (system.getTimer() - tt.t0) / 1000
  out[#out + 1] = string.format('window %.1f s, %d frames, hud still the one wrapped = %s',
    secs, tt.frames, tostring(_G.__hud == tt.hud))
  if tt.frames > 0 then
    out[#out + 1] = string.format('frame ms: avg %.2f  min %.2f  worst %.2f (at %.1f s)',
      tt.fsum / tt.frames, tt.fmin or 0, tt.fmax, (tt.fmax_at or 0) / 1000)
  end
  out[#out + 1] = ''
  out[#out + 1] = string.format('%-12s %6s %8s %8s %8s %8s', 'feature', 'period', 'calls', 'total', 'avg', 'WORST')
  local names = {}
  for n in pairs(tt.stats) do names[#names + 1] = n end
  table.sort(names)
  for _, n in ipairs(names) do
    local r = tt.stats[n]
    out[#out + 1] = string.format('%-12s %6d %8d %8.1f %8.4f %8.3f%s',
      n, r.period, r.calls, r.total, r.calls > 0 and r.total / r.calls or 0, r.max,
      r.err and ('   ERROR ' .. r.err) or '')
  end
  out[#out + 1] = ''
  out[#out + 1] = 'per-second cost of each feature (calls * avg / seconds):'
  for _, n in ipairs(names) do
    local r = tt.stats[n]
    if r.calls > 0 then
      out[#out + 1] = string.format('  %-12s %7.2f ms/s', n, r.total / secs)
    end
  end
  return table.concat(out, '\n')
end

return 'timing ' .. #tt.wrapped .. ' feature update(s): ' .. table.concat(tt.wrapped, ' ')
