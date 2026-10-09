"""Tests de l'estimation du niveau des cartouches.

Ces tests s'appuient sur les valeurs REELLES relevees sur une Canon MF660C
Series le 08/10/2026 :

- compteurs de vie : ``301 Impression = 1208``, ``113 mono = 323``,
  ``123 couleur = 914``, ``501 lecture = 46`` ;
- table de compteurs du journal de cartouche : ``C2: 4 / 819`` — le champ
  « pages » est le nombre de pages portees par le jeu de cartouches en place
  (valide empiriquement : 3 pages imprimees -> 816 devient 819) ;
- rendements retenus : 3 500 pages (noir) et 2 500 pages (couleur).

Lancer : ``python -m pytest tests/test_estimation.py``
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "canon_printer"


def _load(module_name: str):
    """Charge un module du composant sans passer par Home Assistant."""
    spec = importlib.util.spec_from_file_location(module_name, COMPONENT / f"{module_name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


estimation = _load("estimation")
canon_rui = _load("canon_rui")

# Table de compteurs telle qu'elle apparait en fin de page du journal de cartouche
# (capture reelle, avant l'impression de 3 pages).
LOG_TABLE_HTML = """
<table>
  <tr><th>C2:</th><td>00004</td><td>0000000816</td></tr>
  <tr><th>C3:</th><td>00000</td><td>0000000000</td></tr>
  <tr><th>C9 :</th><td>00000</td><td>0000000000</td></tr>
</table>
"""

MEASURED_RUI = {
    "counters": {
        "301 Impression (Total 1)": 1208,
        "113 Total (Noir et blanc/Petit format)": 323,
        "123 Total (Quadrichromie + monochromie/Petit format)": 914,
        "501 Lecture (Total 1)": 46,
    },
    "cartridge_set_pages": 819,
    "cartridge_set_type": "C2",
    "cartridge_set_install_date": "10/05 2026 15:01",
}


def test_parse_set_counters_reads_real_table():
    """Le champ 2 de la ligne C2 est bien le compteur de pages du jeu."""
    counters = canon_rui.CanonRuiClient._parse_set_counters(LOG_TABLE_HTML)
    assert counters["C2"] == {"units": 4, "pages": 816}
    assert counters["C3"] == {"units": 0, "pages": 0}
    assert counters["C9"] == {"units": 0, "pages": 0}


def test_parse_set_counters_ignores_header_rows():
    """Aucune ligne d'en-tete ne doit etre prise pour un compteur."""
    header = "<table><tr><td>(2) Type</td><td>C2</td><td>Inconnu</td></tr></table>"
    assert canon_rui.CanonRuiClient._parse_set_counters(header) == {}


def test_current_set_comes_from_newest_cartridge():
    """Le type de jeu courant est celui de la cartouche la plus recemment montee."""
    data = canon_rui.CanonRuiData(
        reachable=True,
        counters=MEASURED_RUI["counters"],
        cartridge_set_counters={"C1": {"units": 4, "pages": 418}, "C2": {"units": 4, "pages": 819}},
        cartridges={
            "cyan": [
                canon_rui.CartridgeRecord(type="C2", first_used="10/05 2026 15:01"),
                canon_rui.CartridgeRecord(type="C1", first_used="18/02 2026 09:06"),
            ]
        },
    )
    assert data.current_set_type == "C2"
    assert data.pages_with_current_set == 819
    assert data.current_set_install_date == "10/05 2026 15:01"


def test_estimation_matches_measured_values():
    """Noir 76,6 % / couleurs 75,8 % avec les valeurs mesurees du 08/10/2026."""
    estimates = estimation.estimate_cartridges(MEASURED_RUI, 3500, 2500)
    assert set(estimates) == {"black", "cyan", "magenta", "yellow"}
    assert round(estimates["black"].remaining_percent, 1) == 76.6
    assert estimates["black"].remaining_pages == 2681
    for color in ("cyan", "magenta", "yellow"):
        assert round(estimates[color].remaining_percent, 1) == 75.8
        assert estimates[color].remaining_pages == 1894


def test_no_estimate_when_data_missing():
    """Jamais d'estimation inventee : mieux vaut aucune valeur."""
    assert estimation.estimate_cartridges({}, 3500, 2500) == {}
    assert (
        estimation.estimate_cartridges({"counters": MEASURED_RUI["counters"]}, 3500, 2500)
        == {}
    )
    assert estimation.estimate_cartridges({"cartridge_set_pages": 819}, 3500, 2500) == {}


def test_estimation_never_negative():
    """Une cartouche au-dela de son rendement plafonne a 0 %, jamais en negatif."""
    estimates = estimation.estimate_cartridges(
        {**MEASURED_RUI, "cartridge_set_pages": 99999}, 3500, 2500
    )
    assert estimates["black"].remaining_percent == 0.0
    assert estimates["black"].remaining_pages == 0


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
