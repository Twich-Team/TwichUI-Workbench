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
IDENTIFIER=re.compile(r'\b[A-Za-z_]\w*\b')
LOCAL=re.compile(r'^\s*local\s+([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)(?:\s*=|\s*$)')
PUBLIC_CALL=re.compile(r'^\s*([A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)+)\s*\(')


@lru_cache(maxsize=64)
def usage_examples(raw):
    """Exact existing test calls, not inferred signatures or invented behavior."""
    text=raw.decode()
    masked=LITERALS.sub(lambda m:re.sub(r'[^\n]',' ',m[0]),text).splitlines()
    lines=text.splitlines()
    spans=function_ranges(raw)
    declarations=surrounding(raw)
    examples=[]
    for index,line in enumerate(masked):
        match=PUBLIC_CALL.match(line)
        if not match or any(a<=index+1<=b for a,b in spans):
            continue
        end=index+1
        balance=line[match.end()-1:].count('(')-line[match.end()-1:].count(')')
        while balance>0 and end<len(lines) and end-index<8:
            balance+=masked[end].count('(')-masked[end].count(')')
            end+=1
        if balance!=0:
            continue
        first=max(1,index)
        last=min(len(lines),end+2)
        excerpt='\n'.join(lines[first-1:last])
        if len(excerpt)>1600:
            continue
        roots={re.split(r'[.:]',match[1])[0]}
        receiver=[]
        for _ in range(3):
            for declaration in declarations:
                if declaration['names'] & roots and declaration not in receiver:
                    receiver.append(declaration)
                    roots.update(declaration['references'])
        if roots & {'io','os','debug','package','require','loadfile'}:
            continue  # File/process APIs are not addon-interface examples.
        context=[{k:d[k] for k in ('line','through_line','text')} for d in receiver
                 if len(d['text'])<=1600][:6]
        examples.append(dict(name=match[1],call_line=index+1,line=first,
                             through_line=last,text=excerpt,receiver_context=context))
        if len(examples)==16:
            break
    return examples


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
                code[start-1][match.end():]+'\n'+'\n'.join(code[start:end]))),
            references=set(IDENTIFIER.findall('\n'.join(code[start-1:end])))))
    return found



@lru_cache(maxsize=64)
def surrounding(raw):
    """Literal enclosing declarations/registrations, not a Lua interpreter.

    Compiler ranges distinguish module statements from function internals. All
    returned text is exact committed source; large/unfinished values stay omitted.
    """
    lines=raw.decode().splitlines()
    code=LITERALS.sub(lambda m:re.sub(r'[^\n]',' ',m[0]),raw.decode()).splitlines()
    spans=function_ranges(raw)
    result=[]
    for index,line in enumerate(code):
        number=index+1
        containing=[(a,b) for a,b in spans if a<=number<=b]
        # A callback registration can start a compiler function on this line.
        prefix=line.split('function',1)[0]
        callback=bool(CALL.match(prefix.strip())) and all(a==number for a,b in containing)
        if containing and not callback:
            continue
        declaration=LOCAL.match(line)
        exported=re.match(r'^\s*[A-Za-z_]\w*(?:[.:][A-Za-z_]\w*)+\s*=(?!=)',line)
        if not declaration and not exported and not callback:
            continue
        end=max([number,*[b for a,b in containing]])
        chunk='\n'.join(code[index:end])
        balance=sum(chunk.count(c) for c in '({[')-sum(chunk.count(c) for c in ')}]')
        while balance>0 and end<len(lines) and end-number<79:
            balance+=sum(code[end].count(c) for c in '({[')-sum(code[end].count(c) for c in ')}]')
            end+=1
        text='\n'.join(lines[index:end])
        if balance>0 or end-number>=80 or len(text)>4000:
            continue
        names=set(re.findall(r'[A-Za-z_]\w*',declaration[1])) if declaration else set()
        rhs=line.split('=',1)[1].strip() if '=' in line else ''
        alias=bool(re.fullmatch(NAME,rhs)) and rhs not in {'nil','true','false'}
        result.append(dict(line=number,through_line=end,text=text,names=names,
            references=set(IDENTIFIER.findall('\n'.join(code[index:end]))),
            state=bool(declaration and not alias),export=bool(exported and not declaration),registration=callback))
    return result

def context(packet, original, programs=(), *, focus_paths=None):
    from workbench import bug_source_planning as source,bug_investigation as bug
    from workbench.bug_evidence import InvestigationRead
    rows=[]
    module_rows=[]
    examples=[]
    example_groups=[]
    example_chars=0
    for item in packet['files']:
        if not item['path'].endswith('.lua'):
            continue  # Python/scripts/metadata have no Lua compiler or interface claims.
        if item['path'].startswith('tests/test_') and (not focus_paths or item['path'] in focus_paths):
            raw,actual=source.committed(item['path'],packet['base'])
            if actual['sha256']!=item['sha256']:
                raise ValueError('Selected usage-example source changed')
            example_groups.append([dict(**example,path=item['path'],sha256=item['sha256'])
                                   for example in usage_examples(raw)])
        if not item['path'].startswith(('!!!TwichUI/','TwichUI_Settings/')):
            continue
        raw,actual=source.committed(item['path'],packet['base'])
        if actual['sha256']!=item['sha256']:
            raise ValueError('Selected interface source changed')
        rows.extend(dict(**row,path=item['path'],sha256=item['sha256']) for row in definitions(raw))
        module_rows.extend(dict(**row,path=item['path'],sha256=item['sha256']) for row in surrounding(raw))
    for index in range(16):
        for group in example_groups:
            if index>=len(group):
                continue
            example=group[index]
            size=len(example['text'])+sum(len(row['text']) for row in example['receiver_context'])
            if len(examples)<16 and example_chars+size<=8000:
                examples.append(example)
                example_chars+=size
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
        if (row['path'] in focus_paths if focus_paths else bug.words(re.split(r'[.:]',row['name'])[-1])&terms):
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
    for level in range(2):
        selected=list(rank)
        for index,row in enumerate(rows):
            if any(row['name'] in rows[j]['calls'] and (
                    '.' in row['name'] or ':' in row['name'] or row['path']==rows[j]['path']) for j in selected):
                rank[index]=min(rank.get(index,0),-40+level*5)
    # Keep readers with the writers of their enclosing state. Namespace aliases
    # are excluded from state-writer expansion; they would pull every method.
    selected=list(rank)
    reader_names={path:set().union(*(rows[j]['references'] for j in selected if rows[j]['path']==path))
                  for path in {r['path'] for r in rows}}
    for index,row in enumerate(rows):
        analysis=LITERALS.sub(lambda m:re.sub(r'[^\n]',' ',m[0]),row['text'])
        for declaration in module_rows:
            shared=declaration['names'] & reader_names[row['path']]
            if declaration['path']==row['path'] and declaration['state'] and any(
                    re.search(r'(?m)^\s*'+re.escape(name)+r'\s*=(?!=)',analysis) for name in shared):
                importance=min(rank[j] for j in selected if rows[j]['path']==row['path']
                    and rows[j]['references'] & shared)+5
                rank[index]=min(rank.get(index,0),importance)
    selected=list(rank)
    references={path:set().union(*(rows[j]['references'] for j in selected if rows[j]['path']==path))
                for path in {r['path'] for r in rows}}
    dependencies=[]
    for _ in range(2):
        for declaration in module_rows:
            refs=references.setdefault(declaration['path'], set())
            if (declaration['names'] & refs or (declaration['export'] or declaration['registration']) and declaration['references'] & refs):
                if declaration not in dependencies:
                    dependencies.append(declaration)
                refs.update(declaration['references'])
    specs=[]
    specs.extend((row,-4000,'committed public usage example') for row in examples)
    specs.extend((dict(**declaration,path=row['path'],sha256=row['sha256']),-4000,
                  'committed public usage example') for row in examples for declaration in row['receiver_context'])
    used=0
    for declaration in dependencies:
        if used+len(declaration['text'])<=8000:
            specs.append((declaration,-3000,'committed enclosing declaration or registration'))
            used+=len(declaration['text'])
    specs.extend((rows[i],rank[i],'complete selected committed function') for i in sorted(rank,
        key=lambda i:(rank[i],rows[i]['through_line']-rows[i]['line'],rows[i]['path'],rows[i]['line']))[:16])
    priority=[]
    for row,context_rank,coverage in specs:
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
                through_line=row['through_line'],text=row['text'],coverage=coverage,
                context_rank=context_rank))
    return priority,dict(declarations=catalog,omitted_literal_declarations=omitted,
        committed_usage_examples=[dict({k:v for k,v in row.items() if k not in {'text','receiver_context'}},
            receiver_context=[{k:v for k,v in declaration.items() if k!='text'}
                              for declaration in row['receiver_context']]) for row in examples],
        surrounding_context=[dict(path=r['path'],line=r['line'],through_line=r['through_line']) for r in dependencies],
        notice='Exact literal declarations from selected committed files, not runtime export proof. '
               'Dynamic extensions and functions in other files may exist. '
               'Committed usage examples show exact existing test call forms and surrounding source, not invented signatures '
               'or proof that tests passed. Reusing that exact call does not require disclosing a separate definition. '
               'Inspect definitions before inventing a different call or assuming behavior beyond the example; independent checks remain required.')
