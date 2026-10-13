"""Reconduction des dernieres donnees SNMP connues quand l'imprimante se taît.

Un poll SNMP qui n'obtient **aucune** reponse n'est pas un succes : c'est une
perte de contact. Sans traitement explicite, la lecture renvoie des valeurs
vides (aucune OID lue) que Home Assistant publie telles quelles — les capteurs
passent a « unknown » le temps d'un cycle (5 min) puis reviennent, et rien
n'indique que l'imprimante est injoignable.

Regle appliquee : **un poll sans reponse ne publie jamais ses valeurs vides** ;
il reconduit les dernieres donnees connues, marquees ``is_online: False`` avec
l'horodatage du dernier succes. Sans aucun succes connu, rien n'est invente :
l'appelant doit alors signaler l'echec au lieu de publier du vide.

Module volontairement pur (aucune dependance Home Assistant) pour etre testable.
"""

from __future__ import annotations

from typing import Any


def build_offline_data(
    cached_data: dict[str, Any] | None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Donnees d'un poll sans reponse, construites sur le dernier succes connu.

    ``cached_data`` est le contenu du Store (``{"data": ..., "timestamp": ...}``).
    Retourne ``None`` s'il n'y a aucun succes anterieur : on ne fabrique pas de
    donnees, c'est a l'appelant de traiter le cas (echec du poll).

    ``extra`` permet d'injecter des blocs calcules pendant la tentative (par
    exemple l'etat de l'IU distante, qui doit refleter la derniere TENTATIVE et
    non la derniere reussite).
    """
    cached = (cached_data or {}).get("data") or {}
    if not cached:
        return None

    data: dict[str, Any] = dict(cached)
    if extra:
        data.update(extra)

    data["is_online"] = False
    data["offline_since"] = (cached_data or {}).get("timestamp")
    data["offline_error"] = error
    return data
