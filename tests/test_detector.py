"""Tests for the framework detector."""

import os
import tempfile

from api_discover.detector import detect_rails

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "sample_app")


def test_detects_rails_from_gemfile_lock():
    is_rails, version = detect_rails(FIXTURES)
    assert is_rails is True
    assert version == "7.0.4.3"


def test_detects_rails_from_gemfile_only():
    with tempfile.TemporaryDirectory() as tmpdir:
        gemfile = os.path.join(tmpdir, "Gemfile")
        with open(gemfile, "w") as f:
            f.write("gem 'rails', '~> 6.1'\n")
        is_rails, version = detect_rails(tmpdir)
        assert is_rails is True
        assert version == "~> 6.1"


def test_no_rails_detected():
    with tempfile.TemporaryDirectory() as tmpdir:
        gemfile = os.path.join(tmpdir, "Gemfile")
        with open(gemfile, "w") as f:
            f.write("gem 'sinatra'\n")
        is_rails, version = detect_rails(tmpdir)
        assert is_rails is False


def test_no_gemfile():
    with tempfile.TemporaryDirectory() as tmpdir:
        is_rails, version = detect_rails(tmpdir)
        assert is_rails is False
