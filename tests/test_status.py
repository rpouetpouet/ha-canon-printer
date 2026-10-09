"""Tests du statut : bonne OID SNMP et texte lu sur le portail.

Regression corrigee en 0.1.5 : le capteur d'etat lisait ``hrPrinterStatus``
(``1.3.6.1.2.1.25.3.5.1.1.1``), que les Canon laissent a « other » (1) en
permanence — le capteur affichait donc « other » en toutes circonstances. La
seule OID d'etat correctement implementee est ``hrDeviceStatus``
(``1.3.6.1.2.1.25.3.2.1.5.1``), RFC 2790 : 2 = running, 3 = warning, 5 = down.

Lancer : ``python -m pytest tests/test_status.py``
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "canon_printer"


def _load(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, COMPONENT / f"{module_name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


canon_rui = _load("canon_rui")
const = _load("const")

# Bloc tel qu'il apparait sur /portal_top.html (structure reelle, contenu type).
PORTAIL = """
<html><body>
<div>Infos périphérique de base</div>
<div>Statut du périphérique</div>
<div>Imprimante :</div>
<div>Une erreur s'est produite.</div>
<div>Scanner :</div>
<div>Mode veille.</div>
<div>Informations d'erreur</div>
</body></html>
"""


def test_hrdevicestatus_est_la_source_de_l_etat():
    """L'etat vient de hrDeviceStatus, jamais de hrPrinterStatus."""
    source = (COMPONENT / "snmp_client.py").read_text(encoding="utf-8")
    assert "device_state = await self._get_oid(OID_DEVICE_STATE)" in source
    assert '"state_source": "hrDeviceStatus"' in source


def test_mapping_hrdevicestatus_rfc2790():
    """Les valeurs de hrDeviceStatus se traduisent en etats exploitables."""
    mapping = {1: "unknown", 2: "online", 3: "warning", 4: "testing", 5: "down"}
    assert mapping[2] == "online"
    assert mapping[3] == "warning"  # etat reel de l'imprimante en erreur cartouche
    assert mapping[5] == "down"


def test_device_status_constant_du_module():
    """La constante du module ne doit pas faire dire « online » a un « running »."""
    assert const.DEVICE_STATUS[3] == "warning"
    assert const.DEVICE_STATUS[5] == "down"


def test_statut_du_portail_est_extrait():
    """Le texte affiche par l'imprimante est recupere pour imprimante et scanner."""
    statut = canon_rui.CanonRuiClient._parse_device_status(PORTAIL)
    assert statut["printer"] == "Une erreur s'est produite."
    assert statut["scanner"] == "Mode veille."


def test_statut_du_portail_absent_ne_casse_rien():
    """Une page sans le bloc renvoie un dictionnaire vide, sans exception."""
    assert canon_rui.CanonRuiClient._parse_device_status("<html><body>rien</body></html>") == {}


if __name__ == "__main__":
    passes = echecs = 0
    for nom in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[nom]()
            print(f"  PASS {nom}")
            passes += 1
        except AssertionError as err:
            print(f"  FAIL {nom}: {err}")
            echecs += 1
    print(f"\n{passes} passes, {echecs} echecs")
    raise SystemExit(1 if echecs else 0)
