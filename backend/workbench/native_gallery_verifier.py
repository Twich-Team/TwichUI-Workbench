"""Original-derived gallery controls, separate from untrusted candidate processes.

The parent never imports the candidate. Its expected outcomes remain in a separate
address space. The existing outer verifier denies credential/working-copy reads,
parent-memory access and host network, and settles every descendant afterward.
"""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zlib

SAMPLES = ('reminder-lion-shield', 'reminder-expiring-buff', 'reminder-fishing-rod', 'reminder-low-mana')
FOCUSED = 'tests/test_reminder_gallery.py'
REJECTED = 23


def exact_png(decoder, path):
    """Disable the original artwork reader's lossy alpha cleanup for fixtures."""
    threshold = decoder.THRESHOLD
    try:
        decoder.THRESHOLD = -1
        return decoder.read_png(path)
    finally:
        decoder.THRESHOLD = threshold


def png(width, height, rows, *, alpha=True):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6 if alpha else 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(b''.join(b'\0' + bytes(row) for row in rows), 1)) + chunk(b'IEND', b''))


def nodes(text):
    return ast.parse(text).body


def named_class(text, name):
    return next(n for n in nodes(text) if isinstance(n, ast.ClassDef) and n.name == name)


def static_preservation(original, candidate):
    def dump(node):
        return ast.dump(node, include_attributes=False)
    if dump(named_class(original, 'Gallery')) != dump(named_class(candidate, 'Gallery')):
        raise AssertionError('Original image-quality/catalogue Gallery class must survive unchanged')
    for name in ('png_header', 'tga'):
        before = [dump(n) for n in nodes(original) if isinstance(n, ast.FunctionDef) and n.name == name]
        after = [dump(n) for n in nodes(candidate) if isinstance(n, ast.FunctionDef) and n.name == name]
        if before != after:
            raise AssertionError('Original gallery helper changed: ' + name)
    protected = {'ROOT', 'ASSETS', 'MEDIA', 'ARTDATA', 'TOOL', 'SAMPLE'}
    def assignments(text):
        return [dump(n) for n in nodes(text) if isinstance(n, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in protected for target in n.targets)]
    if assignments(original) != assignments(candidate):
        raise AssertionError('Original artifact paths and four sample identities must survive')
    before, after = named_class(original, 'Rebuild'), named_class(candidate, 'Rebuild')
    if [dump(n) for n in before.bases] != [dump(n) for n in after.bases]:
        raise AssertionError('Rebuild must remain a discoverable unittest TestCase')
    required = {'test_tool_ran', 'test_manifest_and_artdata_are_what_the_tool_builds', 'test_sample_pictures_are_what_the_tool_builds'}
    if not required <= {n.name for n in after.body if isinstance(n, ast.FunctionDef)}:
        raise AssertionError('Original rebuild test inventory must survive')


def subject(mode, root, original, output):
    """Untrusted test process; this process never owns expected outcomes."""
    filename = original if mode == 'original' else root / FOCUSED
    sys.path.insert(0, str(root / 'tools'))
    spec = importlib.util.spec_from_file_location('gallery_subject', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    module.ASSETS = root / 'assets/reminder-gallery'
    module.MEDIA = root / '!!!TwichUI/media/reminders/gallery'
    module.ARTDATA = root / '!!!TwichUI/reminders/ArtData.lua'
    module.TOOL = root / 'tools/make_reminder_gallery.py'
    try:
        if mode in {'rebuild', 'original'}:
            module.Rebuild.tmp = type('FrozenOutputs', (), {'name': str(output)})()
            for method in ('test_manifest_and_artdata_are_what_the_tool_builds', 'test_sample_pictures_are_what_the_tool_builds'):
                getattr(module.Rebuild(method), method)()
        elif mode == 'quality':
            module.Gallery.setUpClass()
            module.Gallery.art = [module.Gallery.art[0]]
            module.ROOT = output
            module.Gallery('test_source_images').test_source_images()
        elif mode == 'catalogue':
            module.ASSETS = output / 'assets/reminder-gallery'
            module.Gallery.setUpClass()
            module.Gallery('test_manifest_shape').test_manifest_shape()
        else:
            raise ValueError('Unknown fixed subject mode')
    except (AssertionError, ValueError, IndexError, struct.error, zlib.error):
        return REJECTED
    return 0


def snapshot(directory):
    result = {}
    entries = total = 0
    for path in directory.rglob('*'):
        entries += 1
        if entries > 128:
            raise AssertionError('Subject exceeded the verification input inventory')
        if path.is_symlink():
            raise AssertionError('Subject changed verification input type')
        if path.is_file():
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1 << 20:
                    raise AssertionError('Subject changed verification input file bounds')
                data = bytearray()
                while chunk := os.read(descriptor, 65536):
                    data.extend(chunk)
                    if len(data) > 1 << 20:
                        raise AssertionError('Subject grew verification input beyond bounds')
                total += len(data)
                if total > 4 << 20:
                    raise AssertionError('Subject exceeded aggregate verification input bounds')
                result[str(path.relative_to(directory))] = (hashlib.sha256(data).hexdigest(), stat.S_IMODE(info.st_mode))
            finally:
                os.close(descriptor)
    return result


def check_subject(mode, root, original, output, *, accepts):
    before = snapshot(output)
    command = [sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--subject', mode,
        str(root), str(original), str(output)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True,
        env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C.UTF-8',
            'TMPDIR': os.environ.get('TMPDIR', '/tmp')})
    captured = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 15
            while selector.get_map():
                if time.monotonic() > deadline:
                    raise AssertionError('Candidate subject reached its bounded timeout')
                for key, _ in selector.select(.1):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                    elif len(captured) + len(data) > 65536:
                        raise AssertionError('Candidate subject exceeded bounded diagnostics')
                    else:
                        captured.extend(data)
        rc = process.wait(timeout=1)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=2)
        process.stdout.close()
    if snapshot(output) != before:
        raise AssertionError('Candidate subject changed independent verification inputs')
    if rc != (0 if accepts else REJECTED):
        raise AssertionError('Corrected repository test returned the wrong result: ' + str(rc)
            + '; expected ' + ('acceptance' if accepts else 'assertion rejection') + '\n'
            + captured.decode(errors='replace')[-2000:])


def quality_controls(root, original, read_png):
    checked = []
    doc = json.loads((root / 'assets/reminder-gallery/manifest.json').read_text())
    first = doc['artwork'][0]
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        manifest = output / 'assets/reminder-gallery/manifest.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps(doc))
        check_subject('catalogue', root, original, output, accepts=True)
        for label in ('duplicate-id', 'invalid-category'):
            changed = json.loads(json.dumps(doc))
            if label == 'duplicate-id':
                changed['artwork'].append(changed['artwork'][0])
            else:
                changed['artwork'][0]['category'] = 'invalid-category'
            manifest.write_text(json.dumps(changed))
            check_subject('catalogue', root, original, output, accepts=False)
            checked.append('catalogue:' + label)
        manifest.write_text(json.dumps(doc))
        image = output / first['source']
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes((root / first['source']).read_bytes())
        width, height, rows, alpha = read_png(root / first['source'])
        rows = [bytearray(row) for row in rows]
        check_subject('quality', root, original, output, accepts=True)
        variants = [
            ('dimensions', png(width + 1, height, [row + row[-4:] for row in rows])),
            ('missing-alpha', png(width, height, [bytes(v for i, v in enumerate(row) if i % 4 != 3) for row in rows], alpha=False)),
            ('empty-image', png(width, height, [bytes(width * 4)] * height)),
        ]
        painted = [bytearray(row) for row in rows]
        painted[0][3] = 255
        variants.append(('paint-on-edge', png(width, height, painted)))
        opaque = [bytearray(row) for row in rows]
        for row in opaque:
            row[3::4] = bytes([255]) * width
        variants.append(('lost-transparency', png(width, height, opaque)))
        for label, data in variants:
            image.write_bytes(data)
            check_subject('quality', root, original, output, accepts=False)
            checked.append('source-quality:' + label)
    return checked


def main(root, original):
    static_preservation(original.read_text(), (root / FOCUSED).read_text())
    # This module is unchanged original production source, never the candidate
    # test. Import it BEFORE any untrusted child exists, in this parent only.
    sys.path.insert(0, str(root / 'tools'))
    import make_map_tool_icons as decoder
    checked = []
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory)
        paths = ['assets/reminder-gallery/manifest.json', '!!!TwichUI/reminders/ArtData.lua']
        for sample in SAMPLES:
            paths += ['assets/reminder-gallery/' + sample + '.png']
            paths += ['!!!TwichUI/media/reminders/gallery/' + sample + suffix + '.tga' for suffix in ('', '-small')]
        for name in paths:
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((root / name).read_bytes())
        def run(accepts):
            check_subject('rebuild', root, original, output, accepts=accepts)
        def rejects(label, path, replacement):
            before = path.read_bytes()
            try:
                path.write_bytes(replacement)
                run(False)
                checked.append(label)
            finally:
                path.write_bytes(before)
        run(True)
        checked.append('unchanged-valid-output')
        for index, sample in enumerate(SAMPLES):
            path = output / 'assets/reminder-gallery' / (sample + '.png')
            width, height, rows, alpha = exact_png(decoder, root / 'assets/reminder-gallery' / (sample + '.png'))
            rows = [bytearray(row) for row in rows]
            if not alpha:
                raise AssertionError('Original artwork must have its alpha channel')
            recompressed = png(width, height, rows)
            if recompressed == path.read_bytes():
                raise AssertionError('Recompression did not change compressed bytes')
            path.write_bytes(recompressed)
            run(True)
            checked.append(sample + ':identical-RGBA-recompression-accepted')
            if index == 0:
                check_subject('original', root, original, output, accepts=False)
                checked.append('original-test-rejects-identical-RGBA-recompression')
            rejects(sample + ':changed-width', path, png(width + 1, height, [row + row[-4:] for row in rows]))
            rejects(sample + ':changed-height', path, png(width, height + 1, rows + [rows[-1]]))
            flat = b''.join(rows)
            reshaped = [flat[start:start + width * 8] for start in range(0, len(flat), width * 8)]
            rejects(sample + ':changed-shape-same-RGBA-bytes', path, png(width * 2, height // 2, reshaped))
            for channel, name in enumerate(('red', 'green', 'blue', 'alpha')):
                changed = [bytearray(row) for row in rows]
                changed[height // 2][4 * (width // 2) + channel] ^= 1
                rejects(sample + ':changed-' + name, path, png(width, height, changed))
            if rows[0][3] != 0:
                raise AssertionError('Original source must retain a transparent edge')
            for channel, name in enumerate(('red', 'green', 'blue')):
                hidden_rgb = [bytearray(row) for row in rows]
                hidden_rgb[0][channel] ^= 1
                rejects(sample + ':changed-' + name + '-under-zero-alpha', path, png(width, height, hidden_rgb))
            faint = next(((y, x) for y, row in enumerate(rows)
                for x in range(width) if 1 <= row[4 * x + 3] <= 8), None)
            if faint is None:
                raise AssertionError('Original source must retain a faint nonzero-alpha pixel')
            y, x = faint
            for channel, name in enumerate(('red', 'green', 'blue', 'alpha')):
                changed = [bytearray(row) for row in rows]
                changed[y][4 * x + channel] ^= 1
                rejects(sample + ':changed-faint-' + name, path, png(width, height, changed))
            rgb = [bytes(v for i, v in enumerate(row) if i % 4 != 3) for row in rows]
            rejects(sample + ':missing-alpha', path, png(width, height, rgb, alpha=False))
            rejects(sample + ':truncated-PNG', path, recompressed[:24])
        for sample in SAMPLES:
            for suffix in ('', '-small'):
                path = output / '!!!TwichUI/media/reminders/gallery' / (sample + suffix + '.tga')
                changed = bytearray(path.read_bytes())
                changed[-1] ^= 1
                rejects(sample + suffix + ':changed-runtime-TGA', path, bytes(changed))
        for name in paths[:2]:
            path = output / name
            rejects(name + ':changed-exact-bytes', path, path.read_bytes() + b'\n')
    checked.extend(quality_controls(root, original, decoder.read_png))
    print(json.dumps(dict(independent_profile='native-gallery-v1', checks=checked, count=len(checked)), sort_keys=True))


if __name__ == '__main__':
    if sys.argv[1] == '--subject':
        raise SystemExit(subject(sys.argv[2], *map(Path, sys.argv[3:6])))
    main(*map(Path, sys.argv[1:3]))
