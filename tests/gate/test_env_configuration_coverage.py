"""
Every environment variable the Gate config reads is documented.

An undocumented knob is one an operator cannot find. This is the drift that
happens quietly: a field is added to ``GateClientConfig``, wired into the env
loader, and never reaches ``.env.example``, so the only way to discover it is
to read the source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_SOURCE = REPO_ROOT / "src" / "constellation_node_sdk" / "gate" / "config.py"
ENV_EXAMPLE = REPO_ROOT / ".env.example"

_ENV_READERS = (
    r'os\.getenv\("([A-Z0-9_]+)"',
    r'_env_bool\("([A-Z0-9_]+)"',
    r'_env_optional_int\("([A-Z0-9_]+)"',
)


def _env_vars_read_by_config() -> set[str]:
    source = CONFIG_SOURCE.read_text(encoding="utf-8")
    found: set[str] = set()
    for pattern in _ENV_READERS:
        found.update(re.findall(pattern, source))
    return found


def _env_vars_documented() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return set(re.findall(r"^([A-Z0-9_]+)=", text, flags=re.MULTILINE))


def test_config_reads_at_least_the_known_gate_variables() -> None:
    """Guard the guard: a broken regex would make the check below vacuous."""
    read = _env_vars_read_by_config()
    assert {"GATE_URL", "GATE_CLIENT_TIMEOUT_SECONDS", "GATE_ALLOWED_DESTINATION"} <= read


@pytest.mark.parametrize("variable", sorted(_env_vars_read_by_config()))
def test_every_env_var_the_config_reads_is_documented(variable: str) -> None:
    assert variable in _env_vars_documented(), (
        f"{variable} is read by gate/config.py but absent from .env.example"
    )
