"""Tests de la session de l'IU distante et de la reference des valeurs connues.

Motif reel (26/10/2026) : les 4 capteurs d'estimation de toner et le journal de
cartouches sont passes a `unavailable` / `unknown` a 12:02 et n'en sont sortis
qu'au redemarrage de Home Assistant a 14:53, alors que le SNMP repondait
normalement. Deux causes distinctes, corrigees ici :

1. l'IU distante ne signale PAS une session expiree par une erreur HTTP : elle
   redirige (302) vers /t_sessError.html, qui repond **200** avec « La session a
   expiré. ». Le client croyait donc avoir lu une page valide, gardait
   `_logged_in = True` et servait des donnees VIDES a chaque poll, sans fin ;
2. la lecture vide ecrasait la reference `last_rui` : les polls suivants
   n'avaient plus rien a reconduire, donc plus aucune valeur a afficher.

Lancer : /home/batman/venvs/printer/bin/python tests/test_rui_session.py
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

COMPOSANT = Path(__file__).resolve().parents[1] / "custom_components" / "canon_printer"


def _load(nom: str, fichier: str):
    spec = importlib.util.spec_from_file_location(nom, COMPOSANT / fichier)
    module = importlib.util.module_from_spec(spec)
    sys.modules[nom] = module
    spec.loader.exec_module(module)
    return module


canon_rui = _load("canon_rui", "canon_rui.py")
rui_cache = _load("rui_cache", "rui_cache.py")

is_session_error_page = canon_rui.is_session_error_page
merge_rui_state = rui_cache.merge_rui_state
update_reference = rui_cache.update_reference
reference_from_cache = rui_cache.reference_from_cache
REQUIRED = rui_cache.REQUIRED_VALUE_KEYS

# Page reellement servie par l'impression (verifiee le 26/10/2026 :
# curl -L http://192.168.42.27/cartridge_log.html -> 302 t_sessError.html -> 200).
PAGE_SESSION = """<html><head><title>IU distante : Connexion : MF660C Series</title></head>
<body><div id="contents"><p>La session a expiré.</p>
<form action="/checkLogin.cgi" method="post"><input type="password" name="i2101"></form>
</div></body></html>"""

PAGE_COMPTEURS = """<html><body><table>
<tr><td>Total</td><td>01377</td></tr>
<tr><td>Noir et blanc</td><td>01190</td></tr>
<tr><td>Quadri</td><td>00187</td></tr>
</table></body></html>"""


def _lecture_complete() -> dict:
    return {
        "reachable": True,
        "counters": {"Noir et blanc": 1190, "Quadri": 187},
        "cartridges": {
            "cyan": [{"serial": "----------", "genuine": False}],
            "black": [{"serial": "----------", "genuine": False}],
        },
        "cartridge_set_counters": {"C2": {"units": 4, "pages": 708}},
        "cartridge_set_pages": 708,
    }


# --- 1. detection de la page « session expiree » ---------------------------


def test_page_session_expiree_est_detectee_par_le_texte():
    assert is_session_error_page(PAGE_SESSION) is True


def test_page_session_expiree_est_detectee_par_l_url_finale():
    # L'URL finale seule suffit (redirection vers /t_sessError.html), meme si le
    # corps ne contient pas la phrase (version anglaise, page vide...).
    assert is_session_error_page("", "http://192.168.42.27/t_sessError.html") is True


def test_page_session_expiree_en_anglais():
    assert is_session_error_page("<p>The session has expired.</p>") is True


def test_une_page_normale_n_est_pas_une_session_expiree():
    assert is_session_error_page(PAGE_COMPTEURS) is False
    assert is_session_error_page(PAGE_COMPTEURS, "http://192.168.42.27/d_counter.html") is False


def test_le_formulaire_de_login_n_est_pas_une_page_de_session_expiree():
    # La page de login contient legitimement un champ i2101 : c'est le texte
    # « session expiree » qui decide, pas la presence du formulaire.
    login = PAGE_SESSION.replace("La session a expiré.", "Connexion")
    assert is_session_error_page(login) is False


# --- 2. une lecture vide ne doit pas detruire la reference -----------------


def test_reference_inchangee_sur_lecture_incomplete():
    reference = _lecture_complete()
    vide = merge_rui_state(
        {"reachable": True, "counters": {}, "cartridges": {},
         "cartridge_set_counters": {}, "cartridge_set_pages": None},
        reference,
        required=REQUIRED,
    )
    assert vide["stale"] is True, "une lecture incomplete doit etre marquee perimee"
    assert update_reference(reference, vide) == reference, "la reference ne doit pas bouger"


def test_reference_inchangee_sur_echec_de_lecture():
    reference = _lecture_complete()
    echec = merge_rui_state(
        {"reachable": False, "error": "session expiree"}, reference, required=REQUIRED
    )
    assert echec["stale"] is True
    assert update_reference(reference, echec) == reference


def test_valeurs_indispensables_reconduites_pendant_la_panne():
    reference = _lecture_complete()
    vide = merge_rui_state(
        {"reachable": True, "counters": {}, "cartridges": {},
         "cartridge_set_counters": {}, "cartridge_set_pages": None},
        reference,
        required=REQUIRED,
    )
    # Sans cela, les estimations n'ont plus de donnees et passent a unavailable.
    for cle in REQUIRED:
        assert vide.get(cle) == reference[cle], f"{cle} aurait du etre reconduit"
    assert vide.get("cartridge_set_pages") == 708


def test_deuxieme_lecture_vide_n_efface_rien():
    reference = _lecture_complete()
    for _ in range(3):
        reference = update_reference(
            reference,
            merge_rui_state(
                {"reachable": True, "counters": {}, "cartridges": {},
                 "cartridge_set_counters": {}, "cartridge_set_pages": None},
                reference,
                required=REQUIRED,
            ),
        )
    assert reference["cartridges"], "la reference doit survivre a plusieurs lectures vides"


def test_reference_remplacee_par_une_lecture_complete():
    reference = _lecture_complete()
    nouvelle = _lecture_complete()
    nouvelle["counters"]["Noir et blanc"] = 1200
    nouvelle["cartridge_set_pages"] = 750
    assert update_reference(reference, nouvelle) is nouvelle


# --- 3. amorcage depuis le cache ------------------------------------------


def test_reference_du_cache_ignore_les_champs_vides():
    cache = {"enabled": True, "reachable": True, "counters": {}, "cartridges": {},
             "last_update": "26/10 14:53", "errors": []}
    reference = reference_from_cache(cache)
    assert "cartridges" not in reference, "une lecture vide en cache ne doit pas etre reprise"
    assert "enabled" not in reference
    assert reference["last_update"] == "26/10 14:53"


def test_reference_du_cache_vide():
    assert reference_from_cache(None) == {}
    assert reference_from_cache({}) == {}


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
