-- Interpret reviewed fixture data in an isolated Lua environment. The loaded
-- candidate has neither the verdict channel nor the reference expectations.
local assert, error, type, pairs, ipairs = assert, error, type, pairs, ipairs
local rawget, rawset, pcall, select, unpack = rawget, rawset, pcall, select, unpack
local getmetatable, match = getmetatable, string.match
local setfenv, loadfile, loadstring = setfenv, loadfile, loadstring
local print, exit = print, os.exit
local function copy(t)
  local result = {}
  for k, v in pairs(t) do result[k] = v end
  return result
end
local function values(...)
  return {n=select('#', ...), ...}
end
local function get(root, path)
  for _, key in ipairs(path) do
    if type(root) ~= 'table' then error('missing observation') end
    root = rawget(root, key)
  end
  return root
end
local function parent(root, path)
  for i = 1, #path - 1 do
    if type(root) ~= 'table' then error('missing fixture parent') end
    root = rawget(root, path[i])
  end
  if type(root) ~= 'table' then error('missing fixture parent') end
  return root, path[#path]
end
local function put(root, path, value)
  local owner, key = parent(root, path)
  rawset(owner, key, value)
end
local function arguments(step)
  if #step.fields == 0 then return unpack(step.args, 1, step.args_n) end
  local result = {}
  for _, field in ipairs(step.fields) do
    local node = result
    for i = 1, #field.path - 1 do
      local k = field.path[i]
      if node[k] == nil then node[k] = {} end
      node = node[k]
    end
    put(node, {field.path[#field.path]}, field.value)
  end
  return result
end
local function exercise()
  local env = {assert=assert, error=error, type=type, pairs=pairs, ipairs=ipairs,
    rawget=rawget, rawset=rawset, pcall=pcall, xpcall=xpcall, select=select, unpack=unpack,
    tostring=tostring, tonumber=tonumber, next=next, rawequal=rawequal,
    setmetatable=setmetatable, getmetatable=getmetatable,
    math=copy(math), string=copy(string), table=copy(table), bit=copy(require('bit')),
    coroutine=copy(coroutine), os={clock=os.clock, time=os.time, date=os.date, difftime=os.difftime},
    ROOT=ROOT, REPO=REPO, TESTS=TESTS, print=function() end}
  env._G = env
  env.string.dump = nil
  env.getfenv = function(level)
    assert(level == 0, 'only fixture globals are available')
    return env
  end
  local function load(path, sandbox)
    return setfenv(assert(loadfile(path)), sandbox)
  end
  env.getmetatable = function(value)
    if type(value) == 'string' then return {__index=env.string} end
    return getmetatable(value)
  end
  local loaded = {}
  env.loadfile = function(path)
    assert(type(path) == 'string' and (
      match(path, '^%.%./!!!TwichUI/[A-Za-z0-9_./-]+%.lua$') and not match(path:sub(#ROOT+1), '%.%.')
      or match(path, '^%./[A-Za-z0-9_./-]+%.lua$') and not match(path:sub(3), '%.%.')),
      'unsupported harness load')
    local chunk = load(path, env)
    loaded[chunk] = true
    return chunk
  end
  env.dofile = function(path) return env.loadfile(path)() end
  env.setfenv = function(chunk, scope)
    assert(type(chunk) == 'function' and loaded[chunk] and type(scope) == 'table',
      'only isolated harness chunks can receive a fixture environment')
    return setfenv(chunk, scope)
  end
  env.require = function(name)
    assert(name == 'bit', 'only the test harness bit module is available')
    return env.bit
  end
  -- The harness exposes timer controls in its own globals. Look up inherited
  -- operations on a client without allowing inherited writes during setup.
  local function callable(root, path)
    if #path == 0 then return root end
    local owner, key = parent(root, path)
    return rawget(owner, key) or (owner == root and scenario.fixture == 'client' and rawget(env, key))
  end
  local root, returned, captured, captures = nil, nil, nil, 0
  if scenario.fixture == 'client' then
    if scenario.bootstrap_source and scenario.bootstrap_source ~= '' then
      root = setfenv(assert(loadstring(scenario.bootstrap_source, '@committed-client-setup')), env)()
      assert(type(root) == 'table', 'expected a client from the committed setup')
    else
      load(TESTS .. 'harness.lua', env)()
      root = env.MakeClient('Workbench', {'!!!TwichUI', 'TwichUI_Settings'})
    end
  end
  local function observe(item)
    if item.source == 'field' then return get(root, item.path) end
    local output = returned
    if item.source == 'captured' then
      assert(captures == 1, 'expected exactly one notification or callback')
      output = captured
    end
    assert(output and item.path[1] <= output.n, 'missing return or callback argument')
    return get(output, item.path)
  end
  local namespace, reproduced = {}, false
  for index, step in ipairs(scenario.steps) do
    if step.op == 'load' then
      if scenario.fixture == 'module' then
        root = load('../' .. step.source, env)()
      else
        load('../' .. step.source, root)(match(step.source, '^[^/]+'), namespace)
      end
    elseif step.op == 'table' then
      put(root, step.path, {})
    elseif step.op == 'set' then
      put(root, step.path, step.value)
    elseif step.op == 'stub' then
      put(root, step.path, function() return arguments(step) end)
    elseif step.op == 'capture' then
      captures, captured = 0, nil
      put(root, step.path, function(...)
        captures = captures + 1
        if step.method then captured = values(select(2, ...)) else captured = values(...) end
        return arguments(step)
      end)
    elseif step.op == 'call' then
      local fn = callable(root, step.path)
      assert(type(fn) == 'function', 'expected a function')
      if step.method then
        local receiver = parent(root, step.path)
        returned = values(fn(receiver, unpack(step.args, 1, step.args_n)))
      else
        returned = values(fn(unpack(step.args, 1, step.args_n)))
      end
    end
    for _, item in ipairs(scenario.observations) do
      if item.after_step == index then
        local result = observe(item)
        if result ~= item.expected then
          if result ~= item.incorrect then return 'ambiguous' end
          reproduced = true
        end
      end
    end
  end
  return reproduced and 'fail' or 'pass'
end
local ok, verdict = pcall(exercise)
if not ok then
  print('Workbench could not run the approved fixture in the offline harness.')
elseif verdict == 'pass' then
  print('WB_BEHAVIOR bug-reproducer PASS')
  exit(0)
elseif verdict == 'fail' then
  print('WB_BEHAVIOR bug-reproducer FAIL')
  exit(1)
else
  print('Workbench observed a result outside the reviewed expectations.')
end
exit(2)
