"""Resolve local configuration independently of the module layout."""

import os
from pathlib import Path


def project_root():
    """DBAGG_HOME overrides the checkout root; installed wheels use the working directory."""
    if os.getenv("DBAGG_HOME"):
        return Path(os.environ["DBAGG_HOME"]).expanduser().resolve()
    module = Path(__file__).resolve()
    checkout = module.parents[2]
    if (checkout / "pyproject.toml").is_file() and module.parent == checkout / "src" / "dbagg":
        return checkout
    return Path.cwd().resolve()
