"""Tests du parsing de la page d'erreur de l'IU distante Canon.

Regression corrigee en 0.1.4 : la page contient « Informations d'erreur »
plusieurs fois (titre de page, fil d'Ariane, en-tete de section). Le parseur
prenait la PREMIERE occurrence et renvoyait donc le chapeau de navigation
(« Vers le portail », « Se déconnecter », « Courrier électronique a
l'administrateur ») comme un message d'erreur. Consequence reelle : sur une page
SANS erreur, le capteur binaire passait a l'etat « probleme ».

Les extraits ci-dessous reproduisent la structure observee (le contenu est
synthetique ; les valeurs reelles de l'imprimante ne sont pas versionnees).

Lancer : ``python -m pytest tests/test_error_parsing.py``
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


def _page(contenu: str) -> str:
    """Page d'erreur avec son chapeau de navigation, comme sur l'imprimante."""
    return f"""<html><head><title>IU distante : Informations d'erreur : MF660C Series</title></head>
<body>
<div>MF660C Series / MF660C Series / In the placard</div>
<ul><li>Vers le portail</li><li>Se déconnecter</li>
<li>Suivi statut/Annulation</li><li>Courrier électronique à l'administrateur</li></ul>
<h1>Suivi statut/Annulation : Informations d'erreur</h1>
<h2>Informations d'erreur</h2>
<div>Dernière mise à jour :01/01 2026 12:00:00</div>
<div>Une erreur s'est produite.</div>
<h3>Informations d'erreur</h3>
{contenu}
<div>Imprimer</div><div>Statut tâche</div>
</body></html>"""


MESSAGE_CYAN = (
    "Impossible d'établir la communication avec la cartouche cyan.<br>"
    "Retirez la cartouche de toner et insérez-la de nouveau dans le périphérique, "
    "ou remplacez-la.<br>"
    "Si ce message persiste, une cartouche de toner de contrefaçon ou non authentique "
    "Canon est peut-être utilisée.<br>"
    "Les problèmes dus à l'utilisation de cartouches de toner de contrefaçon ne sont "
    "pas couverts par la garantie."
)


def test_erreur_cartouche_extraite_sans_le_chapeau_de_navigation():
    """Le message utile est extrait, le chapeau de page ne l'est pas."""
    messages = canon_rui.CanonRuiClient._parse_errors(_page(MESSAGE_CYAN))
    assert messages, "au moins un message attendu"
    assert messages[0].startswith("Impossible d'établir la communication avec la cartouche cyan.")
    joint = " ".join(messages)
    for bruit in ("Vers le portail", "Se déconnecter", "Suivi statut/Annulation",
                  "Courrier électronique"):
        assert bruit not in joint, f"bruit de navigation presents : {bruit}"


def test_une_seule_erreur_par_cartouche():
    """Un message par cartouche, pas de doublon, pas de phrase de garantie."""
    page = _page(MESSAGE_CYAN + "<br>" + MESSAGE_CYAN)
    messages = canon_rui.CanonRuiClient._parse_errors(page)
    assert len(messages) == 1, messages
    assert "garantie" not in messages[0]


def test_page_sans_erreur_ne_remonte_rien():
    """Regression : une page saine ne doit produire AUCUN message.

    C'est ce cas qui faisait basculer le capteur binaire en « probleme ».
    """
    page = _page("Aucune erreur.")
    assert canon_rui.CanonRuiClient._parse_errors(page) == []


def test_repli_sur_la_phrase_canonique():
    """Sans en-tete de section, seule la phrase canonique est retenue."""
    page = "<html><body>Etat : MF660C Series Vers le portail " \
           "Impossible d'établir la communication avec la cartouche jaune.</body></html>"
    messages = canon_rui.CanonRuiClient._parse_errors(page)
    assert messages == ["Impossible d'établir la communication avec la cartouche jaune."]


def test_quatre_cartouches_remontees_avec_leur_couleur():
    """Les quatre couleurs doivent apparaitre, dans l'ordre de la page."""
    contenu = "<br>".join(
        f"Impossible d'établir la communication avec la cartouche {couleur}."
        for couleur in ("cyan", "magenta", "jaune", "noire")
    )
    messages = canon_rui.CanonRuiClient._parse_errors(_page(contenu))
    assert len(messages) == 4
    for couleur in ("cyan", "magenta", "jaune", "noire"):
        assert any(couleur in m for m in messages)


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
