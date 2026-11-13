"""Committed investigation formats; metadata never grants execution or writes."""
import hashlib
import json
import re
import struct
import zlib

PATTERN = r'^(?:(?:(?:!!!TwichUI|TwichUI_Settings)/[A-Za-z0-9_./!-]+|tests/[A-Za-z0-9_./-]+)\.lua|(?:tests|tools)/[A-Za-z0-9_./-]+\.(?:py|sh)|assets/[A-Za-z0-9_./-]+\.png)$'
ROOTS = ['!!!TwichUI', 'TwichUI_Settings', 'tests', 'tools', 'assets']


def eligible(path, *, legacy=False):
    return bool(re.fullmatch(PATTERN, path) and
                not any(p.startswith('.') for p in path.split('/')) and
                not {'lib', 'libs', 'vendor'} & {p.lower() for p in path.split('/')} and
                (not legacy or path.endswith('.lua')))


def inventory(root, base, paths=None, *, legacy=False):
    """One exact-commit inventory for selection, navigation, search and reads.

    Eligibility is metadata, not successful decoding or disclosure authority.
    An empty explicit scope stays empty. Git truncation fails rather than proving
    absence. Legacy search receipts retain their original Lua-only interpretation.
    """
    from workbench.regression_preparation import git
    roots = sorted(paths) if paths is not None else ROOTS[:3] if legacy else ROOTS
    raw = git(root, 'ls-tree', '-rz', '-l', base, '--', *roots, max_bytes=1 << 20) if roots else b''
    result = {}
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        meta, name = entry.split(b'\t', 1)
        mode, kind, oid, size = meta.decode().split()
        path = name.decode()
        if (mode in {'100644', '100755'} and kind == 'blob' and int(size) <= 131072
                and eligible(path, legacy=legacy) and
                (paths is None or any(path == p or path.startswith(p+'/') for p in paths))):
            result[path] = dict(git_blob=oid, bytes=int(size))
    return dict(sorted(result.items()))


def decoded(root, path, oid):
    from workbench.regression_preparation import blob, git
    raw = git(root, 'cat-file', 'blob', oid) if path.endswith('.png') else blob(root, oid)
    content = png_metadata(raw) if path.endswith('.png') else raw
    content.decode('utf-8')  # No replacement decoding or binary-as-text fallback.
    return raw, content


def contract(base, entries, permitted=None):
    """Versioned semantics, bound to the exact eligible/disclosable inventory."""
    from workbench.visual_scope import digest
    entries = {p:v for p,v in entries.items() if permitted is None or p in permitted}
    return dict(version=1, base=base, search_version=2, inventory_sha256=digest(entries),
        eligible_files=len(entries), inventory_complete=True, max_blob_bytes=131072,
        formats={'lua':'UTF-8 text', 'py':'UTF-8 text', 'sh':'UTF-8 text',
                 'png':'controlled-png-metadata-v1; no decompression or embedded text'},
        search='Literal case-insensitive line search; 12 matches is possibly incomplete; decode failure is unavailable, never absence.',
        reads='Inclusive one-based UTF-8 lines; PNG lines refer only to controlled metadata; bounded paged results.',
        context='Selected excerpts are bounded and incomplete; omitted lines and files do not establish absence.',
        restrictions='Committed regular blobs only; no hidden, vendor, symlink, traversal or oversized paths. Existing disclosure scope applies.',
        disclosure='existing permitted paths only' if permitted is not None else 'local investigation',
        edit_authority=False, execution_authority=False)


def png_metadata(raw):
    """Validate bounded PNG structure/CRCs, disclose only fixed header facts.

    No image decoder, decompression, embedded text or binary bytes reach the model.
    Line numbers address this explicitly labelled metadata, not the binary file.
    """
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('The committed PNG header is invalid.')
    pos, chunks, header = 8, [], None
    while pos+12 <= len(raw) and len(chunks) < 1024:
        length, kind = struct.unpack('>I4s', raw[pos:pos+8])
        end = pos+12+length
        if end > len(raw) or not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind):
            raise ValueError('The committed PNG structure is invalid.')
        data = raw[pos+8:end-4]
        if zlib.crc32(kind+data) & 0xffffffff != struct.unpack('>I',raw[end-4:end])[0]:
            raise ValueError('The committed PNG checksum is invalid.')
        if not chunks:
            if kind != b'IHDR' or length != 13:
                raise ValueError('The committed PNG header is invalid.')
            header = struct.unpack('>IIBBBBB', data)
            if not 1 <= header[0] <= 32768 or not 1 <= header[1] <= 32768:
                raise ValueError('The committed PNG dimensions exceed inspection limits.')
        chunks.append(kind.decode('ascii'))
        pos = end
        if kind == b'IEND':
            if length or pos != len(raw) or 'IDAT' not in chunks:
                raise ValueError('The committed PNG ending is invalid.')
            break
    if not chunks or chunks[-1] != 'IEND':
        raise ValueError('The committed PNG is incomplete.')
    width,height,depth,color,compression,filtering,interlace = header
    value = dict(format='PNG', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                 width=width, height=height, bit_depth=depth, color_type=color,
                 compression_method=compression, filter_method=filtering, interlace=interlace,
                 chunk_count=len(chunks), decoded_pixels='not inspected')
    return ('Controlled committed PNG metadata; binary bytes and embedded text withheld.\n'
            + json.dumps(value, sort_keys=True)+'\n').encode()
