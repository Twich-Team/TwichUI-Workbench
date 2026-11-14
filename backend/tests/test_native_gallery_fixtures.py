"""Pixel-preserving fixtures and separate untrusted subjects; no provider calls."""
from types import SimpleNamespace

import pytest

from workbench import native_gallery_verifier as verifier


def test_fixture_preserves_hidden_rgb_and_every_faint_alpha_value():
    pixels = bytes(v for alpha in range(256) for v in (112, 68, 35, alpha))
    decoder = SimpleNamespace(THRESHOLD=8)

    def read_png(path):
        row = bytearray(pixels)
        for offset in range(0, len(row), 4):
            if row[offset + 3] <= decoder.THRESHOLD:
                row[offset:offset + 4] = bytes(4)
        return 256, 1, [row], True

    decoder.read_png = read_png
    assert bytes(read_png(None)[2][0]) != pixels
    width, height, rows, alpha = verifier.exact_png(decoder, None)
    assert (width, height, alpha) == (256, 1, True)
    assert bytes(rows[0]) == pixels
    assert bytes(read_png(None)[2][0]) != pixels


def test_failed_fixture_decode_preserves_normal_artwork_reader_behavior():
    decoder = SimpleNamespace(THRESHOLD=8)

    def read_png(path):
        raise ValueError('Malformed synthetic PNG')

    decoder.read_png = read_png
    with pytest.raises(ValueError, match='Malformed'):
        verifier.exact_png(decoder, None)
    assert decoder.THRESHOLD == 8


def test_untrusted_subject_cannot_silently_rewrite_control_inputs(tmp_path):
    root = tmp_path / 'candidate'
    (root / 'tests').mkdir(parents=True)
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'control').write_bytes(b'original fixture')
    candidate = root / verifier.FOCUSED
    candidate.write_text('''from pathlib import Path
import unittest
class Rebuild(unittest.TestCase):
 def test_manifest_and_artdata_are_what_the_tool_builds(self):
  (Path(self.tmp.name) / 'control').write_bytes(b'corrupted fixture')
 def test_sample_pictures_are_what_the_tool_builds(self): pass
''')
    with pytest.raises(AssertionError, match='changed independent verification inputs'):
        verifier.check_subject('rebuild', root, candidate, output, accepts=True)
