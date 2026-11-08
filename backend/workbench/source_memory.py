"""Keep exact committed lines across otherwise stateless model requests."""


def retain(wire, packet, current, budget=24000, *, requested=()):
    lines, priority = {}, {}
    by_path = {item['path']: item for item in wire['files']}
    fresh = {(w['path'],w['line'],w['through_line']) for w in requested}

    def add(path, excerpt, rank):
        for offset, text in enumerate(excerpt['text'].splitlines()):
            key = (path, excerpt['line']+offset)
            if key in lines and lines[key] != text:
                raise ValueError('Remembered committed source lines disagree.')
            lines[key] = text
            priority[key] = min(priority.get(key, rank), rank)

    for item in packet['files']:
        for excerpt in item['excerpts']:
            rank = 1 if excerpt.get('coverage') == 'module prelude' else 3
            if packet.get('plan_review') == 'source-grounded-lua-v1' and excerpt.get('coverage') in {'module prelude', 'partial module prelude', 'committed test-support prelude'}:
                rank = -5000
            add(item['path'], excerpt, rank)
    for excerpt in packet.get('inspection_windows', []):
        add(excerpt['path'], excerpt, 2)
    for excerpt in current:
        # Explicit completed reads must survive inferred function/dependency
        # context. Otherwise the provider asks again for code we read locally
        # but repeatedly evict before the next stateless request.
        immediate = (excerpt['path'],excerpt['line'],excerpt.get('through_line')) in fresh
        add(excerpt['path'], excerpt, -20000 if immediate else
            -10000 if excerpt.get('coverage') == 'complete committed test bootstrap' else excerpt.get('context_rank',0))
    chosen, used = {}, 0
    for key in sorted(lines, key=lambda key:(priority[key], key[0], key[1])):
        size = len(lines[key])+1
        if used+size <= budget:
            chosen[key] = lines[key]
            used += size
    for path, item in by_path.items():
        numbers = sorted(number for name, number in chosen if name == path)
        excerpts, index = [], 0
        while index < len(numbers):
            start = end = numbers[index]
            text = [chosen[(path,start)]]
            current_read = priority[(path,start)] <= 0
            index += 1
            while index < len(numbers) and numbers[index] == end+1:
                end = numbers[index]
                text.append(chosen[(path,end)])
                current_read |= priority[(path,end)] <= 0
                index += 1
            excerpts.append(dict(line=start,through_line=end,text='\n'.join(text),
                coverage='retained exact committed lines',current_read=current_read))
        item['excerpts'] = excerpts
    available = sum(len(text)+1 for text in lines.values())
    keys=('path','line','through_line','requested_start_line','requested_through_line',
          'file_lines','next_line','cached_prefix_through_line','coverage')
    compact=packet.get('plan_review')=='source-grounded-lua-v1'
    wire['source_memory'] = dict(source_base=packet['base'],available_characters=available,
        retained_characters=used,omitted_characters=available-used,
        read_windows=[{key:window[key] for key in (keys[:3] if compact else keys) if key in window}
                      for window in packet.get('inspection_windows',[])],
        note='Previously inspected exact lines remain in file excerpts. Read windows are provenance, '
             'not a claim that every line is visible. Excerpt endpoints identify actual visible coverage. '
             'Missing ranges never establish absence. No addon state, behavior or expected result is remembered or invented.')
    if compact:
        wire['source_memory']['recent_read_windows']=[{key:w[key] for key in keys if key in w}
            for w in packet.get('inspection_windows',[])[-3:]]
    return wire


def requested_visibility(wire, windows):
    """Verify actual outgoing text, rather than equating a disk read with disclosure."""
    visible = {(f['path'], e['line']+offset):text for f in wire['files']
               for e in f['excerpts'] for offset,text in enumerate(e['text'].splitlines())}
    facts = []
    for window in windows:
        if window.get('coverage') not in {'requested committed lines','partial requested committed lines'}:
            continue
        provided = all(visible.get((window['path'],window['line']+offset)) == text
                       for offset,text in enumerate(window['text'].splitlines()))
        if not provided:
            from workbench.planning_transport import PlanningUnavailable
            raise PlanningUnavailable('Requested source could not be retained in the outgoing context.', 'context_budget')
        facts.append(dict(path=window['path'],line=window['line'],
                          through_line=window['through_line'],provided=True))
    return facts
