-- Reads Factorio's prototype data without the game: runs the data files in a sandbox where everything
-- the game engine normally provides (graphics helpers, sounds, `require`d libraries, `defines`) is a
-- "permissive" object that swallows any access. Prints recipes, machines and resources as JSON.
--
--   lua - <data dir> <pack> <mod:file>...        (file list is read from the driver, see the python side)
--
-- argv: [1] data dir, then entries of the form  load:<mod>:<relative file>  or  update:<mod>:<file>
local root = arg[1]

-- ── permissive object ──
local P = {}
local mt = {}
mt.__index = function(_, k) if type(k) == "string" then return P end return nil end
mt.__newindex = function() end
mt.__call = function() return P end
mt.__len = function() return 0 end
mt.__tostring = function() return "" end
mt.__concat = function(a, b)
  return (type(a) == "string" and a or "") .. (type(b) == "string" and b or "")
end
for _, op in ipairs({"add", "sub", "mul", "div", "mod", "pow", "unm", "idiv"}) do mt["__" .. op] = function() return 0 end end
mt.__lt = function() return false end
mt.__le = function() return false end
setmetatable(P, mt)
local function is_perm(v) return v == P end

-- ── data.raw / data:extend ──
local raw = setmetatable({}, {__index = function() return P end})
data = {
  raw = raw,
  extend = function(_, list)
    if type(list) ~= "table" then return end
    for _, p in ipairs(list) do
      if type(p) == "table" and type(p.type) == "string" and type(p.name) == "string" then
        if rawget(raw, p.type) == nil then raw[p.type] = {} end
        raw[p.type][p.name] = p
        p.__mod = CURRENT_MOD
      end
    end
  end,
}
util = P
defines = P
mods = P
settings = P
require = function() return P end
serpent = P
table.deepcopy = function(t)
  if type(t) ~= "table" or is_perm(t) then return t end
  local c = {}
  for k, v in pairs(t) do c[k] = table.deepcopy(v) end
  return c
end
table_size = function(t) local n = 0 for _ in pairs(t) do n = n + 1 end return n end

-- globals other data files would have defined (animations, picture helpers...) read as permissive
setmetatable(_G, {__index = function() return P end})

local function run_file(path)
  local f, err = loadfile(path)
  if not f then io.stderr:write("load failed " .. path .. ": " .. tostring(err) .. "\n") return end
  local ok, e = pcall(f)
  if not ok then io.stderr:write("error in " .. path .. ": " .. tostring(e) .. "\n") end
end

-- Run an *-updates file one top-level statement at a time so one unsupported statement (graphics,
-- sounds, technologies...) cannot take the recipe tweaks down with it.
local function run_updates(path)
  local fh = io.open(path, "r")
  if not fh then return end
  local buf = {}
  local function flush(text)
    -- chunks are loaded separately, so file-level locals must become globals to stay visible
    text = text:gsub("^%s+", ""):gsub("^local function ", "function "):gsub("^local ", "")
    local fn = load(text, "=" .. path, "t")
    if fn then
      local ok, e = pcall(fn)
      if not ok and os.getenv("FACTORIO_DEBUG") then io.stderr:write(path .. ": " .. tostring(e) .. "\n") end
      return true
    end
    return false
  end
  for line in fh:lines() do
    buf[#buf + 1] = line
    local text = table.concat(buf, "\n")
    -- a statement is complete when it compiles AND the next line starts at column 0 (checked lazily:
    -- we flush as soon as it compiles; continuations such as `{` on the next line only follow
    -- statements that do not compile on their own, e.g. `x =`)
    if not text:match("^%s*$") and load(text, "=chunk", "t") then
      flush(text)
      buf = {}
    end
  end
  fh:close()
end

for i = 2, #arg do
  local kind, mod, file = arg[i]:match("^(%a+):([^:]+):(.+)$")
  CURRENT_MOD = mod
  if kind == "load" then run_file(root .. "/" .. mod .. "/" .. file)
  elseif kind == "update" then run_updates(root .. "/" .. mod .. "/" .. file) end
end

-- ── output ──
local function esc(s) return (s:gsub('[%c"\\]', function(c) return string.format("\\u%04x", c:byte()) end)) end
local function enc(v, depth)
  depth = depth or 0
  local t = type(v)
  if t == "table" then
    if is_perm(v) or depth > 8 then return "null" end
    local n = 0
    for _ in pairs(v) do n = n + 1 end
    if n == 0 then return "[]" end
    local isarr = true
    for i = 1, n do if v[i] == nil then isarr = false break end end
    local parts = {}
    if isarr then
      for i = 1, n do parts[#parts + 1] = enc(v[i], depth + 1) end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, x in pairs(v) do
      if type(k) == "string" and type(x) ~= "function" and not is_perm(x) then
        parts[#parts + 1] = '"' .. esc(k) .. '":' .. enc(x, depth + 1)
      end
    end
    return "{" .. table.concat(parts, ",") .. "}"
  elseif t == "string" then return '"' .. esc(v) .. '"'
  elseif t == "number" then
    if v ~= v or v == math.huge or v == -math.huge then return "null" end
    return tostring(v)
  elseif t == "boolean" then return tostring(v)
  else return "null" end
end

-- keep output small: only the fields the importer reads
local MACHINE = {"name", "type", "crafting_speed", "crafting_categories", "energy_usage", "energy_source",
                 "module_slots", "allowed_effects", "__mod"}
local KEEP = {
  recipe = true,
  resource = {"name", "category", "infinite", "minable", "__mod"},
  ["assembling-machine"] = MACHINE,
  furnace = MACHINE,
  ["rocket-silo"] = MACHINE,
  ["mining-drill"] = {"name", "type", "mining_speed", "resource_categories", "energy_usage", "energy_source",
                      "module_slots", "allowed_effects", "__mod"},
  ["offshore-pump"] = {"name", "type", "pumping_speed", "__mod"},
  module = {"name", "category", "tier", "effect", "__mod"},
  tile = {"name", "fluid", "__mod"},
}
local out = {}
for ptype, spec in pairs(KEEP) do
  local list = {}
  local tbl = rawget(raw, ptype)
  if tbl then
    for name, p in pairs(tbl) do
      if spec == true then list[#list + 1] = p
      else
        local slim = {}
        for _, k in ipairs(spec) do slim[k] = p[k] end
        slim.type = ptype
        list[#list + 1] = slim
      end
    end
  end
  table.sort(list, function(a, b) return tostring(a.name) < tostring(b.name) end)
  out[ptype] = list
end
io.write(enc(out))
