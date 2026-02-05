"""Framework detection: parse Gemfile for Rails gem and version."""

from __future__ import annotations

import logging
import os
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def detect_rails(repo_root: str) -> Tuple[bool, Optional[str]]:
    """Detect if the repo is a Rails app and extract the Rails version.

    Returns (is_rails, version_string).
    """
    gemfile = os.path.join(repo_root, "Gemfile")
    gemfile_lock = os.path.join(repo_root, "Gemfile.lock")

    # Try Gemfile.lock first for exact version
    version = _parse_gemfile_lock(gemfile_lock)
    if version:
        return True, version

    # Fall back to Gemfile
    is_rails, version = _parse_gemfile(gemfile)
    return is_rails, version


def _parse_gemfile_lock(path: str) -> Optional[str]:
    """Parse Gemfile.lock for rails gem version."""
    if not os.path.isfile(path):
        return None

    try:
        with open(path, "r") as f:
            content = f.read()
    except OSError:
        return None

    # Look for `rails (X.Y.Z)` in the specs section
    match = re.search(r"^\s+rails \((\d+\.\d+[^)]*)\)", content, re.MULTILINE)
    if match:
        return match.group(1)

    return None


def _parse_gemfile(path: str) -> Tuple[bool, Optional[str]]:
    """Parse Gemfile for rails gem declaration."""
    if not os.path.isfile(path):
        return False, None

    try:
        with open(path, "r") as f:
            content = f.read()
    except OSError:
        return False, None

    # Match: gem 'rails', '~> 7.0' or gem "rails", "7.0.4"
    patterns = [
        r"""gem\s+['"]rails['"](?:\s*,\s*['"]([^'"]+)['"])?""",
        r"""gem\s+['"]railties['"](?:\s*,\s*['"]([^'"]+)['"])?""",
    ]

    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            version = match.group(1) if match.group(1) else None
            return True, version

    return False, None
