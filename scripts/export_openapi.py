import json
from pathlib import Path
from workbench.api import create_app

Path("frontend/openapi.json").write_text(json.dumps(create_app().openapi(), indent=2) + "\n")
