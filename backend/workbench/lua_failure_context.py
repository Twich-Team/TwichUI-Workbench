"""Failure-only working-test observations. Never a replacement independent check.

The fixed interpreter, prelude and test come from the approved recipe. Code runs
in the existing read-only, network-denied check sandbox, with no extra source or
command capability. A real failure stays a failure; values are untrusted data.
"""
import json
import re
from pathlib import Path


def command(argv, focused):
    if (not re.fullmatch(r'tests/test_[A-Za-z0-9_-]+\.lua', focused)
            or len(argv) != 4 or argv[0:2] != ['lua5.1', '-e']
            or argv[3] != Path(focused).name):
        raise ValueError('Only the existing approved focused Lua check can emit local observations')
    script = r'''
local function scalar(v)
    if type(v) == 'string' then return string.format('%q', string.sub(v, 1, 100)) end
    if type(v) == 'number' or type(v) == 'boolean' or type(v) == 'nil' then return tostring(v) end
    return '<' .. type(v) .. '>'
end
local function value(v, depth, seen)
    if type(v) ~= 'table' then return scalar(v) end
    depth, seen = depth or 0, seen or {}
    if seen[v] then return '<cycle>' end
    if depth >= 3 then return '<table>' end
    seen[v] = true
    local keys, count = {}, 0
    for k in next, v do
        count = count + 1
        if (type(k) == 'number' or type(k) == 'string') and type(rawget(v,k)) ~= 'function' then keys[#keys+1] = k end
        if count >= 256 then break end
    end
    table.sort(keys, function(a,b) return type(a)==type(b) and a<b or type(a)<type(b) end)
    local parts, size = {}, 0
    for i,k in ipairs(keys) do
        if i <= 6 or i > #keys-6 then
            local part = scalar(k)..'='..value(rawget(v,k), depth+1, seen)
            local limit = depth == 0 and 2200 or (depth == 1 and 600 or 300)
            if size+#part > limit then parts[#parts+1]='<remaining entries omitted>'; break end
            parts[#parts+1], size = part, size+#part
        end
    end
    seen[v] = nil
    return '{observed_entries='..count..(count==256 and '+' or '')..', '..table.concat(parts, ', ')..'}'
end
local function failure(err)
    local out = {debug.traceback(tostring(err), 2)}
    local used = 0
    for level=2,16 do
        local info = debug.getinfo(level, 'Sl')
        if not info then break end
        if info.source == '@'..WB_FOCUSED then
            local locals = {}
            -- Lua permits 200 locals. Long existing tests can put the failing
            -- value after local96; inspect the whole frame before selecting.
            for i=1,256 do
                local name,v = debug.getlocal(level,i)
                if not name then break end
                if string.sub(name,1,1) ~= '(' then locals[#locals+1] = {name=name, value=v} end
            end
            -- Spend the bounded diagnostic on the most recently declared
            -- values first. Never invoke user metamethods while observing them.
            for i=#locals,math.max(1,#locals-24),-1 do
                local line = 'WB_LOCAL_CONTEXT '..info.short_src..':'..info.currentline..' '..locals[i].name..'='..value(locals[i].value)
                if used+#line <= 6200 then out[#out+1]=line; used=used+#line end
            end
        end
    end
    return table.concat(out, '\n')
end
local ok, err = xpcall(function() dofile(WB_FOCUSED) end, failure)
if not ok then io.stderr:write(err, '\n'); os.exit(1) end
'''
    return argv[:3] + ['-e', 'local WB_FOCUSED='+json.dumps(Path(focused).name)+'\n'+script]
