local seen, seenF, q, hits = {}, {}, {}, {}
local function push(v, p, d)
  local tv = type(v)
  if tv == 'table' then
    if seen[v] then return end
    seen[v] = true
  elseif tv == 'function' then
    if seenF[v] then return end
    seenF[v] = true
  else
    return
  end
  q[#q+1] = {v=v, p=p, d=d}
end
local roots = {[_G]='_G', [package.loaded]='pkg', [debug.getregistry()]='reg'}
for t, n in pairs(roots) do push(t, n, 0) end
local head, nodes, MAXN, MAXD = 1, 0, 250000, 8
local function why(t)
  if type(t) ~= 'table' then return nil end
  local mt = getmetatable(t)
  if type(mt)=='table' then
    local ix = rawget(mt,'__index')
    if type(rawget(mt,'getPotential'))=='function' or (type(ix)=='table' and type(rawget(ix,'getPotential'))=='function') then return 'monster:getPotential' end
  end
  local p = rawget(t,'potential')
  if type(p)=='number' then return 'potential='..p end
  return nil
end
while head <= #q and nodes < MAXN do
  local e = q[head]; head = head + 1; nodes = nodes + 1
  local w = why(e.v)
  if w then hits[#hits+1] = string.format('%s [%s]', e.p, w) end
  if e.d < MAXD then
    if type(e.v) == 'table' then
      for k, v in pairs(e.v) do
        push(v, e.p..'.'..((type(k)=='string') and k or ('['..tostring(k)..']')), e.d+1)
      end
    end
    local i = 1
    if type(e.v) == 'function' then
      while true do
        local n, uv = debug.getupvalue(e.v, i)
        if not n then break end
        push(uv, e.p..'<up:'..n..'>', e.d+1)
        i = i + 1
      end
    end
  end
end
local out = {string.format('nodes=%d hits=%d', nodes, #hits)}
for i = 1, math.min(#hits, 25) do out[#out+1] = hits[i] end
return table.concat(out, '\n')
