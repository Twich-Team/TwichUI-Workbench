"""One explicit synthetic demonstration; ordinary minimal fixtures stay available."""

from copy import deepcopy
import json
from pathlib import Path

from workbench.schemas import DecompositionOutput

PATH = Path(__file__).resolve().parents[2] / "fixtures/intake/guide-status.json"


def representative_result(packet, schema):
    admission = json.loads(PATH.with_name('admission-rehearsal.json').read_text())
    original = packet.get('original', {})
    if all(original.get(key) == value for key, value in admission['request'].items()):
        if schema is DecompositionOutput:
            if packet['proposal'] == admission['planning']['proposal']:
                return deepcopy(admission['decomposition'])
            return None
        if not packet.get('feedback'):
            return deepcopy(admission['planning'])
    example = json.loads(PATH.read_text())
    original = packet.get("original", {})
    if any(original.get(key) != value for key, value in example["request"].items()):
        return None
    # Explicit research revisions must remain research; do not restore implementation scope.
    if schema is DecompositionOutput:
        if (
            packet["proposal"]["delivery"] != "implementation"
            or packet["proposal"]["scope"] != example["planning"]["proposal"]["scope"]
        ):
            return None
        return deepcopy(example["decomposition"])
    if packet.get("feedback"):
        return None  # Ordinary revision path handles the user's actual requested changes.
    return deepcopy(example["planning"])
