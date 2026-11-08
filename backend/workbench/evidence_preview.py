"""Bound browser previews without changing committed evidence or stored bindings."""
LIMIT = 2400
SUFFIX = '\n[… Preview shortened; full source remains bound to the recorded revision.]'


def excerpt(text):
    if len(text) <= LIMIT:
        return text
    return text[:LIMIT-len(SUFFIX)].rstrip() + SUFFIX


def references(items):
    # Historical evidence can predate the preview bound. Never mutate its raw
    # payload or conceal malformed types; only bound an existing text preview.
    return [dict(item, excerpt=excerpt(item['excerpt']))
            if isinstance(item, dict) and isinstance(item.get('excerpt'), str)
            else item for item in items]
