"""Estimation du niveau des cartouches a partir des compteurs de l'imprimante.

Pourquoi estimer : quand la puce d'une cartouche ne repond pas, le niveau est
**indisponible** sur les trois canaux (SNMP ``-2``, IPP ``-1``, IU distante
``-%``) et aucune alerte « toner bas » ne sera jamais emise. La seule
mitigation possible est un calcul, a partir de ce que la machine expose
vraiment.

Ce que la machine expose (verifie sur Canon MF660C Series) :

- le journal de cartouche contient une table ``C2: | 00004 | 0000000819`` dont
  le **2e champ est le nombre de pages portees par le jeu de cartouches** de ce
  type (valide empiriquement : +3 pages imprimees -> +3) ;
- les compteurs ``113`` (pages monochromes) et ``123`` (pages couleur) sont des
  **cumuls de vie** et somment au compteur de vie total.

Modele d'estimation (hypotheses explicites) :

1. toute page imprimee consomme du noir ;
2. seules les pages couleur consomment les toners couleur ;
3. la repartition mono/couleur **du jeu courant** est inconnue (les compteurs
   sont des cumuls de vie) : on retient le mix du cumul de vie comme
   approximation ;
4. le rendement de reference est celui annonce par le fabricant (ISO/IEC 5 %).

C'est une estimation, pas une mesure : la precision depend de la couverture
reelle des pages, qui varie fortement selon les documents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Couleurs suivies dans l'ordre d'affichage (noir puis CMJ).
TONER_COLORS: tuple[str, ...] = ("black", "cyan", "magenta", "yellow")

# Libelles des compteurs RUI, reperes par mot-cle (l'IU est localisee).
_COUNTER_MONO_KEYWORDS = ("noir et blanc",)
_COUNTER_COLOR_KEYWORDS = ("quadri",)


@dataclass(frozen=True)
class TonerEstimate:
    """Estimation pour une couleur donnee."""

    color: str
    remaining_percent: float | None
    remaining_pages: int | None
    consumed_pages: int | None
    reference_yield: int
    cartridge_type: str | None
    install_date: str | None

    def as_attributes(self) -> dict[str, Any]:
        """Attributs exposes par le capteur."""
        return {
            "estimated": True,
            "method": "compteurs imprimante + rendement constructeur",
            "reference_yield_pages": self.reference_yield,
            "estimated_remaining_pages": self.remaining_pages,
            "consumed_pages": self.consumed_pages,
            "cartridge_type": self.cartridge_type,
            "installed": self.install_date,
            "assumptions": [
                "chaque page consomme du noir",
                "seules les pages couleur consomment les toners couleur",
                "repartition mono/couleur du jeu = mix du cumul de vie",
                "rendement constructeur (ISO/IEC 19798, 5 % de couverture)",
            ],
        }


def _find_counter(counters: dict[str, int], keywords: tuple[str, ...]) -> int | None:
    """Retrouve un compteur par mots-cles dans son libelle localise."""
    for label, value in (counters or {}).items():
        low = str(label).lower()
        if all(keyword in low for keyword in keywords):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def estimate_cartridges(
    rui: dict[str, Any] | None,
    yield_black: int,
    yield_color: int,
) -> dict[str, TonerEstimate]:
    """Estime le niveau restant de chaque cartouche.

    Retourne un dictionnaire vide si les donnees indispensables manquent
    (IU distante injoignable, compteur de pages du jeu absent) : mieux vaut
    aucune estimation qu'une estimation inventee.
    """
    rui = rui or {}
    counters = rui.get("counters") or {}

    pages_with_set = rui.get("cartridge_set_pages")
    if pages_with_set is None:
        return {}
    try:
        pages_with_set = int(pages_with_set)
    except (TypeError, ValueError):
        return {}

    mono_lifetime = _find_counter(counters, _COUNTER_MONO_KEYWORDS)
    color_lifetime = _find_counter(counters, _COUNTER_COLOR_KEYWORDS)
    if mono_lifetime is None or color_lifetime is None:
        return {}
    total_lifetime = mono_lifetime + color_lifetime
    if total_lifetime <= 0:
        return {}

    color_fraction = color_lifetime / total_lifetime
    color_pages_with_set = pages_with_set * color_fraction

    cartridge_type = rui.get("cartridge_set_type")
    install_date = rui.get("cartridge_set_install_date")

    estimates: dict[str, TonerEstimate] = {}
    for color in TONER_COLORS:
        if color == "black":
            consumed_pages = float(pages_with_set)
            reference = yield_black
        else:
            consumed_pages = color_pages_with_set
            reference = yield_color

        if reference <= 0:
            continue
        remaining_pages = max(0.0, reference - consumed_pages)
        remaining_percent = max(0.0, min(100.0, remaining_pages / reference * 100))
        estimates[color] = TonerEstimate(
            color=color,
            remaining_percent=round(remaining_percent, 1),
            remaining_pages=int(remaining_pages),
            consumed_pages=int(round(consumed_pages)),
            reference_yield=reference,
            cartridge_type=cartridge_type,
            install_date=install_date,
        )
    return estimates
