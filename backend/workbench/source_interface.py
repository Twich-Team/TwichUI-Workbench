"""Exact selected-source interfaces and function-focused context; never execute Lua.

Literal names are navigation aids, not proof of an export or absence of a dynamic
method. Entire committed function bodies are fetched through the same read owner.
"""
from functools import lru_cache
import json
import re

from workbench.source_context import function_ranges
from workbench.grounded_scenarios import LITERALS

NAME=r'[A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)*'
HEADER=re.compile(r'\bfunction\s+('+NAME+r')\s*\(([^)]*)\)|('+NAME+r')\s*=\s*function\s*\(([^)]*)\)')
CALL=re.compile(r'\b('+NAME+r')\s*\(')


@lru_cache(maxsize=64)
def definitions(raw):
    text=raw.decode()
    masked=LITERALS.sub(lambda m:re.sub(r'[^\n]',' ',m[0]),text)
    lines,code=text.splitlines(),masked.splitlines()
    found=[]
    for start,end in function_ranges(raw):
        if not 1<=start<=end<=len(lines):
            continue
        match=HEADER.search(code[start-1])
        if not match:
            continue
        name=match[1] or match[3]
        args=' '.join((match[2] or match[4] or '').split())
        if len(name)>120 or len(args)>140:
            continue
        found.append(dict(name=name,args=args,line=start,through_line=end,
            text='\n'.join(lines[start-1:end]),calls=tuple(CALL.findall(
                code[start-1][match.end():]+'\n'+'\n'.join(code[start:end])))))
    return found


def context(packet, original, programs=()):
    from workbench import bug_source_planning as source,bug_investigation as bug
    from workbench.bug_evidence import InvestigationRead
    rows=[]
    for item in packet['files']:
        if not item['path'].startswith(('!!!TwichUI/','TwichUI_Settings/')):
            continue
        raw,actual=source.committed(item['path'],packet['base'])
        if actual['sha256']!=item['sha256']:
            raise ValueError('Selected interface source changed')
        rows.extend(dict(**row,path=item['path'],sha256=item['sha256']) for row in definitions(raw))
    catalog={}
    omitted=0
    for row in rows:
        if not re.search(r'[.:]',row['name']):
            continue
        item=f"{row['name']}({row['args']})@{row['line']}-{row['through_line']}"
        proposed={p:list(values) for p,values in catalog.items()}
        proposed.setdefault(row['path'],[]).append(item)
        if len(json.dumps(proposed,separators=(',',':')).encode())<=6000:
            catalog=proposed
        else:
            omitted+=1
    terms=bug.words(original['title']+' '+original['description'])
    rank={}
    for index,row in enumerate(rows):
        if bug.words(re.split(r'[.:]',row['name'])[-1])&terms:
            rank[index]=-100
    # Keep the cause's direct callers and nearby dependencies, not every old
    # line read while investigating unrelated UI/movement helpers.
    for level in range(2):
        selected=list(rank)
        for index,row in enumerate(rows):
            if any(other['name'] in row['calls'] and (
                    '.' in other['name'] or ':' in other['name'] or other['path']==row['path'])
                    for j in selected for other in [rows[j]]):
                rank[index]=min(rank.get(index,0),-80+level*10)
    requested=set()
    for program in programs:
        code=LITERALS.sub(lambda m:re.sub(r'[^\n]',' ',m[0]),program)
        requested.update(CALL.findall(code))
    for index,row in enumerate(rows):
        if row['name'] in requested:
            rank[index]=-200
    selected=list(rank)
    for index,row in enumerate(rows):
        if any(row['name'] in rows[j]['calls'] and (
                '.' in row['name'] or ':' in row['name'] or row['path']==rows[j]['path']) for j in selected):
            rank[index]=min(rank.get(index,0),-40)
    priority=[]
    for index in sorted(rank,key=lambda i:(rank[i],rows[i]['through_line']-rows[i]['line'],rows[i]['path'],rows[i]['line']))[:16]:
        row=rows[index]
        if len(row['text'].encode())>10000:
            continue
        visible=set()
        item=next(f for f in packet['files'] if f['path']==row['path'])
        for w in [*item['excerpts'],*[w for w in packet.get('inspection_windows',[]) if w['path']==row['path']]]:
            visible.update(range(w['line'],w['line']+len(w['text'].splitlines())))
        missing=[n for n in range(row['line'],row['through_line']+1) if n not in visible]
        pos=0
        while pos<len(missing) and len(packet.get('inspection_windows',[]))<source.window_limit(packet):
            first=last=missing[pos]
            pos+=1
            while pos<len(missing) and missing[pos]==last+1 and last-first<79:
                last=missing[pos]
                pos+=1
            if source.source_characters(packet)+len(row['text'])>source.read_limit(packet):
                break
            windows,_,_=source.inspect(packet,[InvestigationRead(path=row['path'],start=first,end=last)])
            if not windows:
                break
            visible.update(range(windows[0]['line'],windows[0]['through_line']+1))
        if set(range(row['line'],row['through_line']+1))<=visible:
            priority.append(dict(path=row['path'],sha256=row['sha256'],line=row['line'],
                through_line=row['through_line'],text=row['text'],coverage='complete selected committed function',
                context_rank=rank[index]))
    return priority,dict(declarations=catalog,omitted_literal_declarations=omitted,
        notice='Exact literal declarations from selected committed files, not runtime export proof. '
               'Dynamic extensions and functions in other files may exist. Read the defining code before assuming an API.')
