"""Compatibility entry point for hosts that import backend.app.

The production Flask application lives at the repository root so the Docker
image can serve both the API and the built Svelte frontend from one process.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT_APP = Path(__file__).resolve().parents[1] / "app.py"
spec = spec_from_file_location("hht_catalog_app", ROOT_APP)
module = module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

app = module.app
run_pipeline = module.run_pipeline
