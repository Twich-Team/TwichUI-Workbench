"""Read installed help and allowlisted public catalog fields. No login or inference.

Print sanitized JSON only; never copies catalog identity, credentials or invocation logs.
Run explicitly and configure WORKBENCH_CAPABILITIES with the output path.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

from workbench.model_policy import CapabilityReport, ExecutorCapability, ModelCapability


def version(binary):
    path = shutil.which(binary)
    if not path:
        return None
    try:
        result = subprocess.run([path, '--version'], capture_output=True, timeout=5, check=True)
        text = result.stdout.decode()+'\n'+result.stderr.decode()
        match = re.search(r'\b\d+\.\d+\.\d+\b', text)
        return match.group() if match else None
    except (OSError, subprocess.SubprocessError):
        return None


def discover(catalog_path=None, config_path=None):
    stamp = datetime.now(timezone.utc).isoformat()
    home = Path.home()
    catalog_path = catalog_path or home / '.codex/models_cache.json'
    config_path = config_path or home / '.codex/config.toml'
    models, model, effort, catalog_at = [], '', None, None
    try:
        if catalog_path.stat().st_size <= 2_000_000:
            catalog = json.loads(catalog_path.read_text())
            catalog_at = catalog.get('fetched_at')
            models = [ModelCapability(model=m['slug'], efforts=[r['effort'] for r in m.get('supported_reasoning_levels', [])])
                      for m in catalog.get('models', [])[:64] if m.get('visibility') == 'list']
        if config_path.stat().st_size <= 100_000:
            config = tomllib.loads(config_path.read_text())
            # Never return provider overrides, endpoints, hooks or identity.
            model, effort = config.get('model', ''), config.get('model_reasoning_effort')
    except (OSError, ValueError, KeyError, TypeError):
        models, model, effort, catalog_at = [], '', None, None
    return CapabilityReport(executors=[
        ExecutorCapability(backend='local', executor='local llama.cpp', version=version(os.environ.get('WORKBENCH_LLAMA_SERVER', 'llama-server')),
            observed_at=stamp, source='Installed --version and existing LocalProvider structured transport contract',
            limitation='Model identity is discovered by the existing adapter only during an authorized request. No model discovery or smoke was run here. WB-15 grounded planning and actual decomposition remain unaccepted; 2/2 approaches exhausted. Other local capabilities remain unknown.'),
        ExecutorCapability(backend='codex', executor='Codex CLI', version=version('codex'), models=models,
            source='Installed Codex model cache · allowlisted fields; catalog is not account entitlement',
            observed_at=catalog_at, default_model=model, default_effort=effort,
            limitation='Cached model/effort support only. Availability, login entitlement and account capacity are unconfirmed. CLI version inspected '+stamp),
        ExecutorCapability(backend='claude', executor='Claude Code', version=version('claude'),
            observed_at=stamp, source='Installed Claude Code --version / --help',
            limitation='CLI help accepts a model identifier and effort flag; no supported model-specific capability catalog was established. Configure a model as not verified; effort stays unspecified. Account allowance is unavailable.')])


if __name__ == '__main__':
    print(discover().model_dump_json(indent=2))
