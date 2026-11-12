"""Committed investigation formats; metadata never grants execution or writes."""
import hashlib
import json
import struct
import zlib

PATTERN = r'^(?:(?:(?:!!!TwichUI|TwichUI_Settings)/[A-Za-z0-9_./!-]+|tests/[A-Za-z0-9_./-]+)\.lua|(?:tests|tools)/[A-Za-z0-9_./-]+\.(?:py|sh)|assets/[A-Za-z0-9_./-]+\.png)$'
ROOTS = ['!!!TwichUI', 'TwichUI_Settings', 'tests', 'tools', 'assets']


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
