"""Tests de l'identite de l'appareil (identity.resolve_unique_id).

Regression couverte : l'identifiant de l'appareil ne doit JAMAIS etre derive de
l'adresse IP. Une imprimante en veille profonde ne repond pas au SNMP ; le
numero de serie manquait alors au poll, l'ancien code retombait sur l'IP et Home
Assistant creait un SECOND appareil (« Unknown Printer », identifiant
``("canon_printer", "192.168.42.27")``) a cote de l'appareil reel indexe par son
numero de serie.

Executer avec l'interpreteur du projet :
    /home/batman/venvs/printer/bin/python tests/test_identity.py
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "canon_printer"


def _install_homeassistant_stubs() -> None:
    """Installe le minimum de `homeassistant` requis par identity.py."""
    ha = types.ModuleType("homeassistant")

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = object  # type: ignore[attr-defined]

    const = types.ModuleType("homeassistant.const")
    const.CONF_HOST = "host"  # type: ignore[attr-defined]

    helpers = types.ModuleType("homeassistant.helpers")
    entity = types.ModuleType("homeassistant.helpers.entity")
    entity.DeviceInfo = dict  # type: ignore[attr-defined]

    ha.config_entries = config_entries  # type: ignore[attr-defined]
    ha.const = const  # type: ignore[attr-defined]
    ha.helpers = helpers  # type: ignore[attr-defined]
    helpers.entity = entity  # type: ignore[attr-defined]

    sys.modules.setdefault("homeassistant", ha)
    sys.modules.setdefault("homeassistant.config_entries", config_entries)
    sys.modules.setdefault("homeassistant.const", const)
    sys.modules.setdefault("homeassistant.helpers", helpers)
    sys.modules.setdefault("homeassistant.helpers.entity", entity)


def _load_module(name: str, path: Path):
    """Charge un module du composant sous un paquet synthetique `cp`."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"module introuvable: {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _import_identity():
    _install_homeassistant_stubs()
    package = types.ModuleType("cp")
    package.__path__ = [str(PKG_DIR)]
    sys.modules.setdefault("cp", package)
    _load_module("cp.const", PKG_DIR / "const.py")
    return _load_module("cp.identity", PKG_DIR / "identity.py")


identity = _import_identity()


class FakeEntry:
    """Entree de configuration minimale (data + entry_id)."""

    def __init__(self, data=None, entry_id="entry-0001"):
        self.data = dict(data or {})
        self.entry_id = entry_id


def test_serial_number_is_used_when_present():
    entry = FakeEntry({"host": "192.168.42.27"})
    data = {"info": {"serial_number": "45V26267"}}
    assert identity.resolve_unique_id(data, entry) == "45V26267"


def test_ip_is_never_used_as_identity():
    """LE test de regression : sans numero de serie, l'IP ne doit pas sortir."""
    entry = FakeEntry({"host": "192.168.42.27"}, entry_id="entry-0001")
    result = identity.resolve_unique_id({"info": {}}, entry)
    assert result == "entry-0001", result
    assert "192.168.42.27" not in result, result


def test_empty_data_falls_back_to_entry_id():
    entry = FakeEntry({"host": "192.168.42.27"}, entry_id="entry-0001")
    assert identity.resolve_unique_id(None, entry) == "entry-0001"
    assert identity.resolve_unique_id({}, entry) == "entry-0001"


def test_offline_payload_keeps_serial_from_info():
    """Un poll hors ligne sert les donnees du cache : la serie doit rester."""
    entry = FakeEntry({"host": "192.168.42.27"})
    data = {"info": {"serial_number": "45V26267"}, "is_online": False}
    assert identity.resolve_unique_id(data, entry) == "45V26267"


def test_stored_serial_used_when_device_is_silent():
    entry = FakeEntry({"host": "192.168.42.27", "serial_number": "45V26267"})
    assert identity.resolve_unique_id({"info": {}}, entry) == "45V26267"


def test_serial_is_cleaned():
    entry = FakeEntry({"host": "192.168.42.27"})
    data = {"info": {"serial_number": "  45V26267\n"}}
    assert identity.resolve_unique_id(data, entry) == "45V26267"


def test_blank_serial_is_ignored():
    entry = FakeEntry({"host": "192.168.42.27"}, entry_id="entry-0001")
    data = {"info": {"serial_number": "   "}}
    assert identity.resolve_unique_id(data, entry) == "entry-0001"


def test_build_device_info_identifier_is_stable():
    entry = FakeEntry({"host": "192.168.42.27"}, entry_id="entry-0001")
    full = {
        "info": {
            "serial_number": "45V26267",
            "description": "Canon MF660C Series /P",
            "name": "Canon MF660C",
            "location": "In the placard",
        },
        "device_name": None,
    }
    device_info = identity.build_device_info(full, entry)
    assert device_info["identifiers"] == {("canon_printer", "45V26267")}, device_info
    assert device_info["serial_number"] == "45V26267"

    # Meme entree, poll sans numero de serie : identifiant DIFFERENT interdit.
    silent = {"info": {"description": "Canon MF660C Series"}}
    device_info_silent = identity.build_device_info(silent, entry)
    assert device_info_silent["identifiers"] == {("canon_printer", "entry-0001")}
    for identifier in device_info_silent["identifiers"]:
        assert "192.168.42.27" not in str(identifier)


if __name__ == "__main__":
    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as err:
            failures += 1
            print(f"ECHEC {test.__name__}: {err}")
        else:
            print(f"ok    {test.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passes")
    sys.exit(1 if failures else 0)
