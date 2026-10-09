"""Tests de la conservation des valeurs de l'IU distante.

Motif reel : un seul echec de lecture de l'IU faisait retomber a « unknown »
tous les capteurs qui en dependent (dont le compteur detaille et le journal de
cartouches), puis a nouveau leur valeur au cycle suivant. Comme l'IU ne repond
pas non plus au premier poll apres un redemarrage de Home Assistant, chaque
redemarrage ajoutait un « unknown » de 5 minutes dans l'historique.

Lancer : python3 tests/test_rui_cache.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

COMPOSANT = Path(__file__).resolve().parents[1] / "custom_components" / "canon_printer"
spec = importlib.util.spec_from_file_location("rui_cache", COMPOSANT / "rui_cache.py")
rui_cache = importlib.util.module_from_spec(spec)
sys.modules["rui_cache"] = rui_cache
spec.loader.exec_module(rui_cache)

merge_rui_state = rui_cache.merge_rui_state

# Forme reelle des donnees de l'IU distante du MF660C (valeurs du 08/10/2026)
DERNIER_CONNU = {
    "enabled": True,
    "reachable": True,
    "last_update": "2026-10-08T23:32:51",
    "counters": {"301 Impression": 1208, "113 Mono": 323, "123 Quadri": 914},
    "cartridges": {
        "noir": [{"first_used": "2026-05-10", "genuine": False, "serial": "----------"}],
        "cyan": [{"first_used": "2026-05-10", "genuine": False, "serial": "----------"}],
    },
    "cartridge_set_counters": {"C2": {"units": 4, "pages": 819}},
    "device_status": {"printer": "Une erreur s'est produite.", "scanner": "Pret a scanner."},
    "errors": ["Impossible d'etablir la communication avec la cartouche cyan."],
}


def test_echec_ponctuel_conserve_les_valeurs_connues():
    """Un echec de l'IU ne doit pas vider les capteurs qui en dependent."""
    resultat = merge_rui_state(
        {"enabled": True, "reachable": False, "error": "session expiree"},
        DERNIER_CONNU,
    )
    assert resultat["counters"] == DERNIER_CONNU["counters"]
    assert resultat["cartridges"] == DERNIER_CONNU["cartridges"]
    assert resultat["cartridge_set_counters"] == DERNIER_CONNU["cartridge_set_counters"]
    assert resultat["device_status"] == DERNIER_CONNU["device_status"]
    assert resultat["errors"] == DERNIER_CONNU["errors"]
    assert resultat["last_update"] == DERNIER_CONNU["last_update"]


def test_la_peremption_est_signalee():
    """On reconduit la valeur, mais jamais en la faisant passer pour fraiche."""
    resultat = merge_rui_state({"enabled": True, "reachable": False}, DERNIER_CONNU)
    assert resultat["stale"] is True
    assert resultat["reachable"] is False  # la derniere TENTATIVE, pas la derniere reussite


def test_une_lecture_reussie_est_retournee_telle_quelle():
    """Pas de reconduction sans echec constate : on ne masque pas la verite."""
    resultat = merge_rui_state({"enabled": True, "reachable": True}, DERNIER_CONNU)
    assert resultat["reachable"] is True
    assert "stale" not in resultat
    assert "counters" not in resultat  # rien n'a ete complete depuis l'ancien etat


def test_aucune_donnee_connue_aucune_invention():
    """Sans valeur connue, on ne fabrique rien : les capteurs restent vides."""
    resultat = merge_rui_state({"enabled": True, "reachable": False, "error": "timeout"}, None)
    assert "counters" not in resultat
    assert "cartridges" not in resultat
    assert "stale" not in resultat
    assert resultat["reachable"] is False

    # idem avec un cache vide (pas de cle du tout)
    vide: dict = {}
    assert merge_rui_state({"enabled": True, "reachable": False}, vide) == {
        "enabled": True,
        "reachable": False,
    }


def test_une_valeur_fraiche_ecrase_la_valeur_reconduite():
    """Sur un echec partiel, ce que la tentative a ramene gagne."""
    resultat = merge_rui_state(
        {"enabled": True, "reachable": False, "counters": {"301 Impression": 1209}},
        DERNIER_CONNU,
    )
    assert resultat["counters"] == {"301 Impression": 1209}          # frais
    assert resultat["cartridges"] == DERNIER_CONNU["cartridges"]      # reconduit
    assert resultat["stale"] is True


def test_le_dernier_etat_n_est_pas_modifie():
    """La fonction ne doit pas alterer son argument (il est reutilise au poll suivant)."""
    copie = {k: v for k, v in DERNIER_CONNU.items()}
    merge_rui_state({"enabled": True, "reachable": False}, DERNIER_CONNU)
    assert DERNIER_CONNU == copie
    assert "stale" not in DERNIER_CONNU


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
