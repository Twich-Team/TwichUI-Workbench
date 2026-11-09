"""Keep mocked API array records within pinned generated return declarations.

This is a verification aid, not live-client validation or a source permission.
Partial/malformed observations remain possible. Undeclared fields cannot supply
positive behavior that the documented game API does not expose.
"""
import hashlib
import json
import re

from workbench.game_api_reference import ENTRY, ROOT

IDENTIFIER = re.compile(r'^[A-Za-z_]\w*$')
FIELD = re.compile(r'\{\s*Name\s*=\s*"(\w+)"[^\n]*')


def source_support(root, base, paths):
    from workbench.game_api_reference import collect, CALL
    from workbench.grounded_scenarios import LITERALS
    from workbench.regression_preparation import git

    files = [dict(excerpts=[dict(text=git(root, 'show', base+':'+path).decode())]) for path in paths]
    namespaces = {namespace for file in files for part in file['excerpts']
                  for namespace, _ in CALL.findall(LITERALS.sub('', part['text']))}
    if not namespaces:
        return {}, []
    return contracts(namespaces), collect(files)


def declarations(text):
    blocks = []
    for match in ENTRY.finditer(text):
        name = re.search(r'Name\s*=\s*"(\w+)"', match[0])
        kind = re.search(r'Type\s*=\s*"(Function|Structure)"', match[0])
        if name and kind:
            blocks.append((name[1], kind[1], match[0]))
    structures = {name: sorted({m[1] for m in FIELD.finditer(body.split('Fields =', 1)[-1])})
                  for name, kind, body in blocks if kind == 'Structure'}
    result = {}
    for name, kind, body in blocks:
        if kind != 'Function' or 'Returns =' not in body:
            continue
        returns = list(FIELD.finditer(body.split('Returns =', 1)[1]))
        arrays = {}
        for index, field in enumerate(returns, 1):
            inner = re.search(r'InnerType\s*=\s*"(\w+)"', field[0])
            if inner and inner[1] in structures:
                arrays[str(index)] = structures[inner[1]]
        if arrays:
            result[name] = arrays
    return result


def contracts(namespaces):
    """Verify public pinned bytes before deriving bounded, non-executable data."""
    manifest = json.loads((ROOT/'reference_data/forever-api.json').read_text())
    result = {}
    cache = ROOT/'.local/game-api-reference'
    if cache.resolve() != cache or cache.is_symlink():
        raise ValueError('Pinned API reference cache unavailable')
    for item in manifest['entries']:
        namespace = item['namespace']
        if namespace not in namespaces:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_]+Documentation\.lua', item['file']):
            raise ValueError('Invalid pinned API reference path')
        path = cache/item['file']
        if path.is_symlink() or not path.is_file():
            raise ValueError('Pinned API reference unavailable')
        raw = path.read_bytes()
        if len(raw) != item['bytes'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('Pinned API reference changed')
        parsed = declarations(raw.decode('utf-8-sig'))
        if parsed:
            result.setdefault(namespace, {}).update(parsed)
    validate(result)
    return result


def validate(value):
    if not isinstance(value, dict) or len(value) > 12 or len(json.dumps(value)) > 12000:
        raise ValueError('API mock contract exceeds its bound')
    for namespace, functions in value.items():
        if not re.fullmatch(r'C_[A-Za-z_]\w*', namespace) or not isinstance(functions, dict) or len(functions) > 128:
            raise ValueError('Invalid API mock namespace')
        for name, returns in functions.items():
            if not IDENTIFIER.fullmatch(name) or not isinstance(returns, dict) or len(returns) > 16:
                raise ValueError('Invalid API mock function')
            for index, fields in returns.items():
                if (not re.fullmatch(r'[1-9]|1[0-6]', index) or not isinstance(fields, list)
                        or not 1 <= len(fields) <= 128 or len(set(fields)) != len(fields)
                        or any(not isinstance(f, str) or not IDENTIFIER.fullmatch(f) for f in fields)):
                    raise ValueError('Invalid API mock return fields')
    return value


def lua(value):
    if isinstance(value, dict):
        return '{'+','.join('['+json.dumps(k)+']='+lua(v) for k, v in sorted(value.items()))+'}'
    return '{'+','.join('['+json.dumps(k)+']=true' for k in value)+'}'


def guard(value):
    validate(value)
    if not value:
        return ''
    # Wrap only the test client's namespaced APIs. Do not execute reference Lua,
    # change production files or fill missing fields with invented values.
    return 'do\nlocal contracts = '+lua(value)+r'''
local real_dofile, real_pairs, real_type = dofile, pairs, type
local real_setmetatable, real_getmetatable = setmetatable, getmetatable
local write, notices = io.write, 0
local pack = function(...) return {n=select('#',...),...} end
local wrapped_factory
local function api(namespace, original)
    if real_type(original) ~= 'table' then return original end
    return real_setmetatable({}, {__index=function(_,name)
        local f = original[name]
        local returns = contracts[namespace][name]
        if not returns or real_type(f) ~= 'function' then return f end
        return function(...)
            local values = pack(f(...))
            for index,fields in real_pairs(returns) do
                local n = tonumber(index)
                local rows = values[n]
                if real_type(rows) == 'table' then
                    local clean = {}
                    for key,row in real_pairs(rows) do
                        clean[key] = row
                        if real_type(row) == 'table' then
                            local filtered = {}
                            for field,v in real_pairs(row) do
                                if real_type(field) ~= 'string' or fields[field] then filtered[field] = v
                                elseif notices < 8 and #field < 80 and field:match('^[A-Za-z_][A-Za-z_0-9]*$') then
                                    notices=notices+1
                                    write('WB_API_MOCK ',namespace,'.',name,' return[',index,'][].',field,
                                        ' omitted: field absent from pinned return declaration\n')
                                end
                            end
                            clean[key] = filtered
                        end
                    end
                    values[n] = clean
                end
            end
            return unpack(values,1,values.n)
        end
    end, __newindex=function(_,key,v) original[key]=v end})
end
local function client(c)
    if real_type(c) ~= 'table' then return c end
    local previous = real_getmetatable(c) or {}
    if real_type(previous) ~= 'table' then error('API mock client metatable unavailable') end
    local meta, boxes = {}, {}
    for k,v in real_pairs(previous) do meta[k]=v end
    for namespace in real_pairs(contracts) do
        boxes[namespace] = api(namespace, c[namespace])
        rawset(c,namespace,nil)
    end
    meta.__index=function(t,k)
        if contracts[k] then return boxes[k] end
        if real_type(previous.__index)=='function' then return previous.__index(t,k) end
        if real_type(previous.__index)=='table' then return previous.__index[k] end
    end
    meta.__newindex=function(t,k,v)
        if contracts[k] then boxes[k]=api(k,v)
        elseif real_type(previous.__newindex)=='function' then previous.__newindex(t,k,v)
        elseif real_type(previous.__newindex)=='table' then previous.__newindex[k]=v
        else rawset(t,k,v) end
    end
    return real_setmetatable(c,meta)
end
dofile=function(path)
    local values=pack(real_dofile(path))
    if real_type(MakeClient)=='function' and MakeClient~=wrapped_factory then
        local original=MakeClient
        wrapped_factory=function(...) return client(original(...)) end
        MakeClient=wrapped_factory
    end
    return unpack(values,1,values.n)
end
end
'''
