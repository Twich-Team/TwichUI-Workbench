"""Keep exact committed lines across otherwise stateless model requests."""


def retain(wire, packet, current, budget=24000):
    lines, priority = {}, {}
    by_path = {item['path']: item for item in wire['files']}

    def add(path, excerpt, rank):
        for offset, text in enumerate(excerpt['text'].splitlines()):
            key = (path, excerpt['line']+offset)
            if key in lines and lines[key] != text:
                raise ValueError('Remembered committed source lines disagree.')
            lines[key] = text
            priority[key] = min(priority.get(key, rank), rank)

    for item in packet['files']:
        for excerpt in item['excerpts']:
            add(item['path'], excerpt, 1 if excerpt.get('coverage') == 'module prelude' else 3)
    for excerpt in packet.get('inspection_windows', []):
        add(excerpt['path'], excerpt, 2)
    for excerpt in current:
        add(excerpt['path'], excerpt, -1 if excerpt.get('coverage') == 'complete committed test bootstrap' else 0)
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
    wire['source_memory'] = dict(source_base=packet['base'],available_characters=available,
        retained_characters=used,omitted_characters=available-used,
        read_windows=[{key:window[key] for key in ('path','line','through_line',
                          'requested_start_line','requested_through_line','file_lines',
                          'next_line','cached_prefix_through_line','coverage') if key in window}
                      for window in packet.get('inspection_windows',[])],
        note='Previously inspected exact lines remain in file excerpts. Read windows are provenance, '
             'not a claim that every line is visible. Excerpt endpoints identify actual visible coverage. '
             'Missing ranges never establish absence. No addon state, behavior or expected result is remembered or invented.')
    return wire
