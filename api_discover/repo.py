"""Repo resolution: clone git URL or validate local path."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)


class RepoResolver:
    """Resolve a repo source to a local path."""

    def __init__(self, source: str, token: Optional[str] = None):
        self.source = source
        self.token = token
        self._temp_dir: Optional[str] = None

    def resolve(self) -> str:
        """Resolve the source to a local path. Raises ValueError on failure."""
        if self._is_url(self.source):
            return self._clone_repo()
        return self._validate_local(self.source)

    def cleanup(self) -> None:
        """Clean up any temporary directories."""
        if self._temp_dir and os.path.isdir(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    def _is_url(self, source: str) -> bool:
        """Check if the source looks like a git URL."""
        return (source.startswith("https://") or
                source.startswith("http://") or
                source.startswith("git@") or
                source.startswith("git://"))

    def _clone_repo(self) -> str:
        """Clone a git repo to a temp directory."""
        self._temp_dir = tempfile.mkdtemp(prefix="api_discover_")
        url = self._inject_token(self.source) if self.token else self.source

        logger.info("Cloning %s ...", self.source)
        try:
            cmd = ["git", "clone", "--depth", "1", url, self._temp_dir]
            env = os.environ.copy()
            if self.token and not self._is_https_with_token(url):
                env["GIT_ASKPASS"] = "echo"
                env["GIT_PASSWORD"] = self.token

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, env=env
            )
            if result.returncode != 0:
                raise ValueError(
                    f"Git clone failed: {result.stderr.strip()}"
                )
        except subprocess.TimeoutExpired:
            raise ValueError("Git clone timed out after 120 seconds")
        except FileNotFoundError:
            raise ValueError("git is not installed or not in PATH")

        return self._validate_local(self._temp_dir)

    def _validate_local(self, path: str) -> str:
        """Validate that a local path is a Rails project."""
        path = os.path.abspath(path)
        if not os.path.isdir(path):
            raise ValueError(f"Path does not exist: {path}")

        routes_file = os.path.join(path, "config", "routes.rb")
        if not os.path.isfile(routes_file):
            raise ValueError(
                f"Not a Rails project (no config/routes.rb): {path}"
            )

        return path

    def _inject_token(self, url: str) -> str:
        """Inject auth token into HTTPS URL."""
        if url.startswith("https://"):
            # https://github.com/org/repo → https://token@github.com/org/repo
            return url.replace("https://", f"https://{self.token}@", 1)
        return url

    @staticmethod
    def _is_https_with_token(url: str) -> bool:
        return "@" in url and url.startswith("https://")
