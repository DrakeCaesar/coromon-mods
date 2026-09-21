local out = {}
local function say(s) out[#out + 1] = s end

local sh = _G.spawnableHelper
say('spawnableHelper=' .. type(sh))
local hunt = sh and sh.findSpawnablesInDirectionUntilBlocking
say('hunt=' .. type(hunt))

-- NOT `local _n, fn = cond and debug.getupvalue(...) or nil`: and/or collapses to ONE value, so the
-- name came back and the function was dropped. Multi-value calls only survive as a bare last
-- expression in an assignment.
local _n, fn = nil, nil
if type(hunt) == 'function' then _n, fn = debug.getupvalue(hunt, 1) end
local ok, p = pcall(function() return sh:getPlayerSpawnable() end)
if type(fn) ~= 'function' or not ok or type(p) ~= 'table' then
  return 'setup failed: fn=' .. type(fn) .. ' player=' .. tostring(ok)
end

local tx, ty = p.currentTileX, p.currentTileY
say(string.format('player tile=%s,%s level=%s', tostring(tx), tostring(ty),
  tostring(p.currentObjectLevel)))

-- The four neighbours, so the flag can be seen going TRUE on a real obstacle rather than only the
-- nil it returns on open ground.
local around = {
  { 'right', tx + 1, ty },
  { 'left',  tx - 1, ty },
  { 'down',  tx,     ty + 1 },
  { 'up',    tx,     ty - 1 },
}

for i = 1, #around do
  local name, nx, ny = around[i][1], around[i][2], around[i][3]
  local ok2, res = pcall(fn, {
    tileX = nx, tileY = ny, widthInTiles = 1, heightInTiles = 1,
    level = p.currentObjectLevel, ignoredObjects = p.ignoredObjects,
  })
  if not ok2 then
    say(string.format('%s(%d,%d) THREW %s', name, nx, ny, tostring(res)))
  elseif type(res) ~= 'table' then
    say(string.format('%s(%d,%d) returned %s', name, nx, ny, type(res)))
  else
    local n = 0
    pcall(function() n = #(res.spawnables or {}) end)
    say(string.format('%s(%d,%d) blocking=%s spawnables=%d',
      name, nx, ny, tostring(res.blocking), n))
  end
end

return table.concat(out, ' | ')
