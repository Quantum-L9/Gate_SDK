"""Gate_SDK cryptography floor stays installable next to IB-Odoo_19.

The Odoo.sh pyOpenSSL window is a *consumer* pin (IB-Odoo_19
``cryptography==43.0.3``), not an SDK-wide ceiling. The SDK only needs
the floor so ``43.0.3`` still resolves. ``42.0.8`` stays rejected.
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
# Patched float consumers may take when they do not pin below 45.
_PATCHED_CRYPTOGRAPHY = Version("50.0.1")


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


def test_odoo_pin_stays_installable_without_sdk_ceiling() -> None:
    spec = _cryptography_specifier()
    assert spec.contains(Version("44.0.0"))
    assert spec.contains(_PATCHED_CRYPTOGRAPHY), (
        f"{spec} must admit {_PATCHED_CRYPTOGRAPHY} so consumers without an "
        "Odoo pin can float to a patched cryptography"
    )
    assert spec.contains(_ODOO_PIN), (
        f"{spec} must still contain Odoo pin {_ODOO_PIN}"
    )
