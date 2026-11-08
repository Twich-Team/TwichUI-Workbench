"""Provide a complete existing test setup, not a generated reproduction or fix."""


def complete(packet, remaining, should_stop, focus_paths=None):
    from workbench import bug_source_planning as source
    from workbench.bug_evidence import InvestigationRead
    candidates = []
    for item in packet['files']:
        setup = item.get('client_bootstrap')
        if not setup:
            continue
        if should_stop():
            raise ValueError('Investigation stopped before committed bootstrap inspection')
        raw, actual = source.committed(item['path'], packet['base'])
        if actual.get('client_bootstrap') != setup:
            raise ValueError('Committed bootstrap metadata changed')
        lines = raw.decode().splitlines()
        end = setup['through_line']
        text = '\n'.join(lines[:end])
        if len(text.encode()) > 16000:
            continue
        coverage = sum(f['path'].removeprefix('!!!TwichUI/') in text for f in packet['files']
                       if f['path'].startswith('!!!TwichUI/') and not f.get('support_only')
                       and (focus_paths is None or f['path'] in focus_paths))
        if coverage:
            candidates.append((-coverage,item['path'],setup,lines,actual))
    if not candidates:
        return [], [], 0, None
    _,path,setup,lines,actual = min(candidates,key=lambda x:(x[0],x[1]))
    visible = set()
    selected = next(f for f in packet['files'] if f['path']==path)
    for window in [*selected['excerpts'], *[w for w in packet.get('inspection_windows',[]) if w['path']==path]]:
        visible.update(range(window['line'],window['line']+len(window['text'].splitlines())))
    missing = [n for n in range(1,setup['through_line']+1) if n not in visible]
    if sum(len(lines[n-1])+1 for n in missing) > remaining:
        return [], [], 0, dict(test_path=path,complete=False,reason='remaining source-read bound')
    windows, chars = [], 0
    index = 0
    while index < len(missing):
        start=end=missing[index]
        index+=1
        while index<len(missing) and missing[index]==end+1 and end-start<79:
            end=missing[index]
            index+=1
        if should_stop():
            raise ValueError('Investigation stopped during committed bootstrap inspection')
        if len(packet.get('inspection_windows',[]))>=source.window_limit(packet):
            raise ValueError('Committed bootstrap exceeds the source window bound')
        returned,_,count=source.inspect(packet,[InvestigationRead(path=path,start=start,end=end)])
        windows.extend(returned)
        chars+=count
    priority=[dict(path=path,sha256=actual['sha256'],line=1,through_line=setup['through_line'],
                   text='\n'.join(lines[:setup['through_line']]),coverage='complete committed test bootstrap')]
    return priority,windows,chars,dict(test_path=path,**setup,complete=True,
        note='Existing committed startup, not a generated reproduction. client is the returned object; do not add its local variable name to access paths.')
