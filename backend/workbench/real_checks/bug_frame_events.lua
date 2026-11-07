-- Track actual registrations on table frames from a replaced test factory.
-- Never create a handler, event, flight path or expected value. Original harness
-- frames remain owned by its original dispatcher; delegated factories do not
-- receive duplicate delivery. This is offline harness support, not game proof.
local client_event_activation = {}
local function activate_client_events(client)
  if client_event_activation[client] then client_event_activation[client]() end
end
local function instrument_client_events(client)
  if type(client) ~= 'table' then return client end
  local base_factory, base_event = client.CreateFrame, client.FireEvent
  if type(base_factory) ~= 'function' or type(base_event) ~= 'function' then return client end
  local old_meta = getmetatable(client)
  assert(old_meta == nil or type(old_meta) == 'table' and old_meta.__newindex == nil,
    'unsupported client write metatable')
  local old_index = old_meta and old_meta.__index
  local active = false
  client_event_activation[client] = function() active = true end
  local factory, wrappers, original_frames, tracked, frames = base_factory, {}, {}, {}, {}
  local function track(frame)
    if original_frames[frame] or tracked[frame] then return end
    assert(type(frame) == 'table' and #frames < 1024, 'unsupported or excessive offline frames')
    local state = {events={}, hooks={}}
    tracked[frame] = state
    frames[#frames+1] = frame
    local function wrap(name, record)
      local method = frame[name]
      assert(type(method) == 'function', 'missing offline frame method')
      frame[name] = function(self, ...)
        local returned = values(method(self, ...))
        record(...)
        return unpack(returned, 1, returned.n)
      end
    end
    wrap('RegisterEvent', function(event)
      assert(type(event) == 'string' and #event <= 128, 'invalid offline event')
      state.events[event] = true
    end)
    wrap('UnregisterEvent', function(event) state.events[event] = nil end)
    wrap('UnregisterAllEvents', function() state.events = {} end)
    wrap('SetScript', function(name, handler)
      if name == 'OnEvent' then
        assert(handler == nil or type(handler) == 'function', 'invalid offline handler')
        state.handler = handler
      end
    end)
    wrap('HookScript', function(name, handler)
      if name == 'OnEvent' then
        assert(type(handler) == 'function' and #state.hooks < 32, 'invalid offline event hook')
        state.hooks[#state.hooks+1] = handler
      end
    end)
  end
  local function wrapped(make)
    if not wrappers[make] then
      wrappers[make] = function(...)
        local frame = make(...)
        if make == base_factory then original_frames[frame] = true else track(frame) end
        return frame
      end
    end
    return wrappers[make]
  end
  local meta = {}
  for key, value in pairs(old_meta or {}) do meta[key] = value end
  meta.__index = function(self, key)
    if key == 'CreateFrame' then return wrapped(factory) end
    if type(old_index) == 'function' then return old_index(self, key) end
    if type(old_index) == 'table' then return old_index[key] end
  end
  meta.__newindex = function(self, key, value)
    if key == 'CreateFrame' then
      assert(type(value) == 'function', 'offline frame factory must remain callable')
      factory = value
    else rawset(self, key, value) end
  end
  rawset(client, 'CreateFrame', nil)
  setmetatable(client, meta)
  client.FireEvent = function(event, ...)
    base_event(event, ...)
    -- Preserve the committed bootstrap's startup exactly. Supplemental event
    -- delivery begins only after setup, for explicitly selected task actions.
    if not active then return end
    -- Snapshot the frames present at dispatch, matching the harness's bounded
    -- synchronous event delivery. Registration changes are observed as called.
    local count = #frames
    for i = 1, count do
      local frame, state = frames[i], tracked[frames[i]]
      if state.events[event] then
        if state.handler then state.handler(frame, event, ...) end
        for _, hook in ipairs(state.hooks) do hook(frame, event, ...) end
      end
    end
  end
  return client
end
