"""Gate_SDK cryptography range must stay installable next to IB-Odoo_19.

Odoo.sh pins ``cryptography==43.0.3`` with ``pyOpenSSL==24.3.0``. That
pyOpenSSL release supports cryptography 41.0.5–44.x. An unbounded
``cryptography>=43`` lets pip pull 45+ and recreate the registry crash
(#114). ``42.0.8`` is below the SDK floor and is not a compatible
downgrade target.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# IB-Odoo_19 requirements.txt / constraints.txt (Staging).
_ODOO_PIN = Version("43.0.3")
# Historic / non-overlapping pin — must stay rejected.
_LEGACY_ODOO_PIN = Version("42.0.8")
# First major outside pyOpenSSL 24.3.0's declared window.
_OUTSIDE_PYOPENSSL_WINDOW = Version("45.0.0")


def _cryptography_specifier() -> SpecifierSet:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    for requirement in data["project"]["dependencies"]:
        if requirement.startswith("cryptography"):
            return SpecifierSet(requirement[len("cryptography") :].strip())
    raise AssertionError("pyproject.toml does not declare cryptography")


def test_odoo_pin_is_inside_the_sdk_range() -> None:
    spec = _cryptography_specifier()
    assert spec.contains(_ODOO_PIN), f"{spec} must contain Odoo pin {_ODOO_PIN}"


def test_legacy_42_pin_is_outside_the_sdk_range() -> None:
    spec = _cryptography_specifier()
    assert not spec.contains(_LEGACY_ODOO_PIN), (
        f"{spec} must reject {_LEGACY_ODOO_PIN}; that pin cannot satisfy this SDK"
    )


def test_range_has_an_upper_bound_inside_pyopenssl_24_3() -> None:
    spec = _cryptography_specifier()
    assert spec.contains(Version("44.0.0"))
    assert not spec.contains(_OUTSIDE_PYOPENSSL_WINDOW), (
        f"{spec} must reject {_OUTSIDE_PYOPENSSL_WINDOW} so pip cannot float "
        "past pyOpenSSL 24.3.0's 41.0.5–44.x window"
    )
