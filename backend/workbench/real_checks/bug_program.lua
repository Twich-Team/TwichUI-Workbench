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
local function program_phases(env, root, scenario, control)
  local phase_events, phase_number = {}, 0
  if scenario.fixture == 'client' then
    local dispatch = root.FireEvent
    assert(type(dispatch) == 'function', 'missing committed event dispatcher')
    root.FireEvent = function(event, ...)
      assert(type(event) == 'string' and #event <= 128, 'invalid fixture event')
      local events = phase_events[phase_number]
      if events then
        assert(#events < 32, 'excessive fixture events')
        events[#events+1] = event
      end
      return dispatch(event, ...)
    end
  end
  local scope = {state={}}
  if scenario.fixture == 'client' then scope.client = root else scope.subject = root end
  scope._G = scope
  setmetatable(scope, {__index=env})
  local reproduced, first_events = false, {}
  for index, phase in ipairs(scenario.phases) do
    phase_number, phase_events[index] = index, {}
    local chunk = assert(loadstring(phase.code, '@reviewed-reproduction-phase'))
    setfenv(chunk, scope)
    control.begin()
    local output
    local ok = pcall(function() output = values(chunk()) end)
    control.finish()
    assert(ok, 'approved reproduction failed')
    local events = phase_events[index]
    if scenario.event_coverage then
      if index == 1 then
        assert(#events == 0, 'initial behavior must precede the reported event')
      elseif index == 2 then
        assert(#events > 0, 'missing first reported event')
        for _, event in ipairs(events) do first_events[event] = true end
      else
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
