-- Fixed interpreter. Source/fixture data are frozen by the existing Runtime.
-- A model draft is not semantic approval; the reviewed expectation digest is separate.
-- Candidate tests run in their own Lua globals. They cannot replace the oracle's
-- verdict/exit primitives or inspect its scenario/wrapper upvalues. OS confinement
-- and the unchanged ordinary suite remain separate Runtime requirements.
local assert, error, type, rawget, pairs, ipairs, select, unpack = assert, error, type, rawget, pairs, ipairs, select, unpack
local pcall, getfenv, setfenv, getmetatable, loadfile, loadstring = pcall, getfenv, setfenv, getmetatable, loadfile, loadstring
local print, exit, match, max = print, os.exit, string.match, math.max
local function copy(source)
  local result = {}
  for key, value in pairs(source) do result[key] = value end
  return result
end
local safe = {next=next, tostring=tostring, tonumber=tonumber, rawset=rawset, rawequal=rawequal,
  getmetatable=getmetatable, setmetatable=setmetatable, type=type, rawget=rawget, ipairs=ipairs,
  pairs=pairs, select=select, unpack=unpack, pcall=pcall, xpcall=xpcall, error=error,
  setfenv=setfenv, print=print, ROOT=ROOT, REPO=REPO, TESTS=TESTS}
local readfile = io.open
local math_source, string_source, table_source, bit_source = copy(math), copy(string), copy(table), copy(require('bit'))
local coroutine_source = copy(coroutine)
string_source.dump = nil
local os_source = {clock=os.clock, time=os.time, date=os.date, difftime=os.difftime}
local function pack(...) return {n = select('#', ...), ...} end
local function field(root, path)
  local value = root
  for _, key in ipairs(path) do
    if type(value) ~= 'table' then error('unsupported observation path') end
    value = rawget(value, key)
  end
  return value
end
local function parent(root, path)
  local value = root
  for i = 1, #path - 1 do
    if type(value) ~= 'table' then error('unsupported target path') end
    value = rawget(value, path[i])
  end
  return value, path[#path]
end
local function call(root, path, args, method, n)
  local fn = field(root, path)
  if type(fn) ~= 'function' then error('expected exported function') end
  if method then local receiver = parent(root, path); return fn(receiver, unpack(args, 1, n)) end
  return fn(unpack(args, 1, n))
end
local function environment(s, mutate)
  local env = copy(safe)
  env.math, env.string, env.table, env.os = copy(math_source), copy(string_source), copy(table_source), copy(os_source)
  if s.fixture == 'loaded-client' then
    env.coroutine = copy(coroutine_source)
    env.bit = copy(bit_source)
    -- The bundled media library asks for the thread globals. Return the
    -- isolated test globals and keep stack/function inspection unavailable.
    env.getfenv = function(level)
      if level ~= 0 then error('scenario adapter exposes only the test globals') end
      return env
    end
  end
  env.getmetatable = function(value)
    if type(value) == 'string' then return {__index=env.string} end
    return getmetatable(value)
  end
  env.io = {open=function(path, mode)
    if mode ~= nil and mode ~= 'r' and mode ~= 'rb' then error('scenario adapter permits read-only fixture files') end
    return readfile(path, mode)
  end}
  env.os.exit = function() error('scenario test exited before its expectations could be checked') end
  env.require = function(name)
    if name ~= 'bit' then error('scenario adapter supports only the existing bit module') end
    return env.bit or copy(bit_source)
  end
  env._G = env
  local hits, failed_assertion = 0, false
  env.assert = function(ok, ...)
    if not ok then failed_assertion = true end
    return assert(ok, ...)
  end
  local function wrap(root)
    local target = field(root, s.target)
    if type(target) ~= 'function' then return root end
    local replacement = function(...)
      local args, n = {...}, select('#', ...)
      local offset = s.method and 1 or 0
      local matches = n == s.args_n + offset
      for i = 1, s.args_n do if args[i+offset] ~= s.args[i] then matches = false end end
      if not matches then return target(...) end
      hits = hits + 1
      if #s.observe.path == 0 then
        local values = pack(target(...))
        values[1] = s.fault
        return unpack(values, 1, max(1, values.n))
      end
      local values = pack(target(...))
      local owner, key = parent(root, s.observe.path)
      if type(owner) ~= 'table' then error('unsupported mutated observation') end
      owner[key] = s.fault
      return unpack(values, 1, values.n)
    end
    if #s.target == 0 then return replacement end
    local owner, key = parent(root, s.target)
    owner[key] = replacement
    return root
  end
  env.dofile = function(path)
    local chunk = assert(loadfile(path))
    setfenv(chunk, env)
    local result = chunk()
    if mutate and s.fixture == 'module' and path == '../' .. s.source_paths[1] then
      result = wrap(result)
    end
    if mutate and s.fixture == 'client' and path == (TESTS or './') .. 'harness.lua' then
      local make = rawget(env, 'MakeClient')
      if type(make) ~= 'function' then error('expected harness MakeClient') end
      env.MakeClient = function(...) return wrap(make(...)) end
    end
    return result
  end
  local client_source = s.fixture == 'loaded-client' and ROOT .. match(s.source_paths[1], '^[^/]+/(.+)$')
  env.loadfile = function(path)
    local chunk, reason = loadfile(path)
    if not chunk then return nil, reason end
    if mutate and path == client_source then
      -- The test sets the chunk's client environment before calling it. Install
      -- the fault only after the real chunk has published its functions there.
      local proxy
      local function loaded(client, ...)
        wrap(client)
        return ...
      end
      proxy = function(...)
        local client = getfenv(proxy)
        setfenv(chunk, client)
        return loaded(client, chunk(...))
      end
      return setfenv(proxy, env)
    end
    return setfenv(chunk, env), reason
  end
  env.loadstring = function(text, name)
    local chunk, reason = loadstring(text, name)
    if chunk then setfenv(chunk, env) end
    return chunk, reason
  end
  return env, function() return hits, failed_assertion end
end
local function exercise(s)
  local env = environment(s, false)
  local root
  if s.fixture == 'module' then root = env.dofile('../' .. s.source_paths[1])
  else
    env.dofile((TESTS or './') .. 'harness.lua')
    root = env.MakeClient(s.name, s.addons)
    if s.fixture == 'loaded-client' then
      local chunk = assert(env.loadfile(ROOT .. match(s.source_paths[1], '^[^/]+/(.+)$')))
      setfenv(chunk, root)
      chunk(s.addons[1], {})
    end
  end
  for _, step in ipairs(s.setup) do
    if step.op == 'set' then
      local owner, key = parent(root, step.path)
      if type(owner) ~= 'table' then error('expected setup table') end
      owner[key] = step.value
    else call(root, step.path, step.args, step.method, step.args_n) end
  end
  local observed = call(root, s.target, s.args, s.method, s.args_n)
  if #s.observe.path > 0 then observed = field(root, s.observe.path) end
  return observed == s.observe.expected
end
local function run_test(s, mutate)
  local env, telemetry = environment(s, mutate)
  local ok, reason = pcall(env.dofile, match(s.test_path, '[^/]+$'))
  local hits, assertion = telemetry()
  return ok, hits, assertion, reason
end
for _, scenario in ipairs(scenarios) do
  local safe, matches = pcall(exercise, scenario)
  if not safe or not matches then
    print('WB_EXPECTATION ' .. scenario.id .. ' MISMATCH')
    exit(1) -- Missing expectation evidence: deliberately no behavioral verdict.
  end
end
local adequate = true
for _, scenario in ipairs(scenarios) do
  local correct = run_test(scenario, false)
  local faulty, hits, assertion = run_test(scenario, true)
  if not correct or faulty or hits == 0 or not assertion then
    adequate = false
    local why = not correct and 'CORRECT_RUN_FAILED' or hits == 0 and 'TARGET_NOT_OBSERVED'
      or faulty and 'OBSERVATION_NOT_ASSERTED' or 'NON_ASSERTION_FAILURE'
    print('WB_SCENARIO ' .. scenario.id .. ' ' .. why)
  end
end
if adequate then print('WB_BEHAVIOR regression-scenarios PASS')
else print('WB_BEHAVIOR regression-scenarios FAIL'); exit(1) end
