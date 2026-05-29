# tests/conftest.py
import sys
from pathlib import Path

import pytest

# Add src to path for imports during testing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from percepxion_mcp import client as client_mod


@pytest.fixture(autouse=True)
def reset_session():
    """Clear auth session state before and after each test."""
    client_mod.session.clear()
    yield
    client_mod.session.clear()


@pytest.fixture()
def authed_session():
    """Return a session pre-loaded with fake tokens."""
    client_mod.session.auth_token = "fake-auth-token"
    client_mod.session.csrf_token = "fake-csrf-token"
    return client_mod.session
