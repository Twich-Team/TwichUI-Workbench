-- Model-authored code runs only after review, in the same isolated fixture VM.
-- Expected values/verdict functions stay outside its environment.
local function prepare_program_environment(env)
  local hook = debug.sethook
  local create, resume = coroutine.create, coroutine.resume
  local instructions, active_hook = 0, nil
  local function budget()
    instructions = instructions + 1000
    if instructions > 2000000 then
      print('Workbench stopped an approved reproduction at its instruction bound.')
      exit(2)
    end
  end
  env.coroutine.create = function(fn)
    local thread = create(fn)
    if active_hook then hook(thread, active_hook, '', 1000) end
    return thread
  end
  env.coroutine.resume = function(thread, ...)
    if active_hook then hook(thread, active_hook, '', 1000) end
    return resume(thread, ...)
  end
  env.coroutine.wrap = function(fn)
    local thread = env.coroutine.create(fn)
    return function(...)
      local result = values(env.coroutine.resume(thread, ...))
      assert(result[1], 'approved coroutine failed')
      return unpack(result, 2, result.n)
    end
  end
  return {
    begin=function()
      instructions, active_hook = 0, budget
      hook(budget, '', 1000)
    end,
    finish=function()
      hook()
      active_hook = nil
    end,
  }
end
local function program_phases(env, root, scenario, control, fresh_session)
  local phase_events, phase_number = {}, 0
  local function track_events(client)
    local dispatch = client.FireEvent
    assert(type(dispatch) == 'function', 'missing committed event dispatcher')
    client.FireEvent = function(event, ...)
      assert(type(event) == 'string' and #event <= 128, 'invalid fixture event')
      local events = phase_events[phase_number]
      if events then
        assert(#events < 32, 'excessive fixture events')
        events[#events+1] = event
      end
      return dispatch(event, ...)
    end
  end
  if scenario.fixture == 'client' then track_events(root) end
  local scope = {state={}}
  local alias
  if scenario.fixture == 'client' then
    scope.client = root
    alias = scenario.bootstrap and scenario.bootstrap.client
    assert(type(alias) == 'string' and alias:match('^[%a_][%w_]*$'), 'invalid committed client alias')
    if alias ~= 'state' and alias ~= 'subject' and alias ~= '_G' then scope[alias] = root end
  else scope.subject = root end
  scope._G = scope
  setmetatable(scope, {__index=function(_, key) return env[key] end})
  local restarts = 0
  if scenario.fixture == 'client' then
    scope.restart_session = function(names, guid)
      assert(type(names)=='table' and #names>=1 and #names<=8 and restarts<6, 'invalid saved-variable restart')
      assert(guid==nil or guid==false or type(guid)=='string' and #guid>0 and #guid<=128, 'invalid fixture identity')
      local nodes, bytes, active, saved = 0, 0, {}, {}
      local function clone(value, depth)
        nodes=nodes+1
        assert(nodes<=20000 and depth<=12, 'saved-variable serialization bound')
        local kind=type(value)
        if kind=='nil' or kind=='boolean' then return value end
        if kind=='number' then
          assert(value==value and value~=math.huge and value~=-math.huge, 'invalid saved number')
          return value
        end
        if kind=='string' then
          bytes=bytes+#value
          assert(#value<=65536 and bytes<=1048576, 'saved-variable byte bound')
          return value
        end
        assert(kind=='table' and not active[value] and getmetatable(value)==nil, 'nonserializable saved data')
        active[value]=true
        local result={}
        for key,item in pairs(value) do
          assert(type(key)=='string' or type(key)=='number', 'invalid saved key')
          result[clone(key,depth+1)]=clone(item,depth+1)
        end
        active[value]=nil
        return result
      end
      for key in pairs(names) do assert(type(key)=='number' and key>=1 and key<=#names and key%1==0, 'invalid saved-variable list') end
      for _,name in ipairs(names) do
        assert(type(name)=='string' and #name<=128 and name:match('^[%a_][%w_]*DB$') and saved[name]==nil, 'invalid saved-variable name')
        saved[name]=clone(rawget(root,name),0)
      end
      restarts=restarts+1
      env,root=fresh_session(saved,guid,env.coroutine)
      track_events(root)
      scope.client=root
      if alias~='state' and alias~='subject' and alias~='_G' then scope[alias]=root end
      return root
    end
  end
  local reproduced, first_events = false, {}
  for index, phase in ipairs(scenario.phases) do
    phase_number, phase_events[index] = index, {}
    local chunk = assert(loadstring(phase.code, '@reviewed-reproduction-phase'))
    setfenv(chunk, scope)
    control.begin()
    local output
    local ok, why = pcall(function() output = values(chunk()) end)
    control.finish()
    if not ok then
      local message=tostring(why)
      local line=message:match('reviewed%-reproduction%-phase:(%d+):') or '?'
      local member=message:match("attempt to [%w ]+ field '([%w_]+)'") or ''
      local category=message:match('attempt to index') and 'invalid-index'
        or message:match('attempt to call') and 'invalid-call'
        or message:match('arithmetic') and 'invalid-arithmetic' or 'fixture-error'
      print('WB_FIXTURE_ERROR phase=' .. index .. ' line=' .. line .. ' category=' .. category .. ' member=' .. member)
    end
    assert(ok, 'approved reproduction failed')
    local events = phase_events[index]
    if scenario.event_coverage then
      if index == 1 then
        assert(#events == 0, 'initial behavior must precede the reported event')
      elseif index == 2 then
        assert(#events > 0, 'missing first reported event')
        for _, event in ipairs(events) do first_events[event] = true end
      elseif index == 3 then
        local repeated = false
        for _, event in ipairs(events) do if first_events[event] then repeated = true end end
        assert(repeated, 'missing repeated reported event')
      end
    end
    print('WB_FIXTURE_PHASE bug-reproducer ' .. index .. ' events=' .. #events)
    local item = scenario.observations[index]
    assert(item and item.after_step == index and output.n == 1, 'expected one measured scalar per phase')
    local result = output[1]
    if result ~= item.expected then
      if result ~= item.incorrect then
        print('WB_OBSERVATION bug-reproducer ' .. index .. ' outside-reviewed-results')
        return 'ambiguous'
      end
      print('WB_OBSERVATION bug-reproducer ' .. index .. ' incorrect')
      reproduced = true
    else
      print('WB_OBSERVATION bug-reproducer ' .. index .. ' expected')
    end
  end
  return reproduced and 'fail' or 'pass'
end
