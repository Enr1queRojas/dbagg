"""Resolve local configuration independently of the module layout."""

import os
from pathlib import Path


def project_root():
    """DBAGG_HOME overrides the checkout root; installed wheels use the working directory."""
    if os.getenv("DBAGG_HOME"):
        return Path(os.environ["DBAGG_HOME"]).expanduser().resolve()
    checkout = Path(__file__).resolve().parents[1]
    if (checkout / "pyproject.toml").is_file():
        return checkout
    return Path.cwd().resolve()
