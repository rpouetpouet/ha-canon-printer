"""Tests de la reconduction des donnees SNMP quand l'imprimante ne repond pas.

Motif reel (13/10/2026) : a 09:54 l'agent SNMP de l'imprimante a cesse de
repondre (veille profonde). Le poll a 09:56 a « reussi » en ramenant des valeurs
vides, que Home Assistant a publiees : le compteur de pages est passe a
« unknown » de 09:56:04 a 10:01:10 (un cycle de 300 s), avant de revenir a sa
valeur. Rien n'indiquait que l'imprimante etait injoignable.

Lancer : python3 tests/test_snmp_cache.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

COMPOSANT = Path(__file__).resolve().parents[1] / "custom_components" / "canon_printer"
spec = importlib.util.spec_from_file_location("snmp_cache", COMPOSANT / "snmp_cache.py")
snmp_cache = importlib.util.module_from_spec(spec)
sys.modules["snmp_cache"] = snmp_cache
spec.loader.exec_module(snmp_cache)

build_offline_data = snmp_cache.build_offline_data

# Forme reelle du cache ecrit par l'integration (valeurs du 13/10/2026)
CACHE = {
    "data": {
        "info": {"page_count": 1304, "serial_number": "45V26267", "state": "warning"},
        "status": {"state": "warning", "device_status_raw": 3},
        "page_count": {"total": 1304, "color": None, "black_and_white": None},
        "display_text": "toner cartridge needs to be checked (yellow).",
        "errors": "4",
        "rui": {
            "enabled": True,
            "reachable": True,
            "counters": {"301 Impression": 1275},
            "cartridge_set_counters": {"C2": {"units": 4, "pages": 862}},
        },
    },
    "timestamp": "2026-10-13T10:47:34",
    "host": "192.168.42.27",
}


def test_un_poll_sans_reponse_ne_publie_pas_de_valeurs_vides():
    """Le poll echoue : on sert le dernier etat connu, jamais ses valeurs vides."""
    resultat = build_offline_data(CACHE, "No SNMP response received before timeout")
    assert resultat["page_count"]["total"] == 1304
    assert resultat["display_text"] == "toner cartridge needs to be checked (yellow)."
    assert resultat["status"]["state"] == "warning"


def test_la_perte_de_contact_est_datee():
    """L'entite doit pouvoir dire DEPUIS QUAND on est sans contact."""
    resultat = build_offline_data(CACHE, "timeout")
    assert resultat["is_online"] is False
    assert resultat["offline_since"] == "2026-10-13T10:47:34"
    assert resultat["offline_error"] == "timeout"


def test_sans_succes_connu_rien_n_est_invente():
    """Aucun etat anterieur : on ne fabrique pas de donnees, l'appelant echoue."""
    assert build_offline_data(None, "timeout") is None
    assert build_offline_data({}, "timeout") is None
    assert build_offline_data({"data": {}}, "timeout") is None
    assert build_offline_data({"data": None}, "timeout") is None


def test_le_bloc_de_la_tentative_ecrase_le_bloc_du_cache():
    """Le bloc calcule pendant la tentative (IU distante) doit refleter la
    derniere TENTATIVE, pas la derniere reussite."""
    resultat = build_offline_data(
        CACHE,
        "timeout",
        extra={"rui": {"enabled": True, "reachable": False, "error": "session expiree"}},
    )
    assert resultat["rui"]["reachable"] is False
    assert resultat["rui"]["error"] == "session expiree"
    # ...sans toucher au reste du cache
    assert resultat["page_count"]["total"] == 1304


def test_le_cache_source_n_est_pas_modifie():
    """Le cache est relu au poll suivant : la fonction ne doit pas l'alterer."""
    import copy

    copie = copy.deepcopy(CACHE)
    build_offline_data(CACHE, "timeout")
    assert CACHE == copie
    assert "is_online" not in CACHE


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
    sys.exit(1 if echecs else 0)
