"""Conservation des dernieres valeurs connues de l'IU distante.

L'IU distante est une source *lente et fragile* : session a renouveler, pages
qui expirent, rate-limiting. Sans precaution, un seul echec de lecture fait
retomber a ``unknown`` **tous** les capteurs qui en dependent, puis a nouveau
leur valeur au cycle suivant -> deux ecritures d'etat par incident, visibles
comme du bruit dans l'historique (et un « unknown » de 5 minutes apres chaque
redemarrage de Home Assistant, le premier poll n'obtenant pas encore l'IU).

Regle appliquee : **on ne perd jamais une valeur connue a cause d'un echec
ponctuel** — on la conserve et on signale honnetement qu'elle est perimee
(``stale`` + ``reachable: False`` + l'horodatage du dernier succes). On
n'invente jamais de valeur : sans donnee connue, on ne fabrique rien.

Module volontairement pur (aucune dependance Home Assistant) pour etre testable.
"""

from __future__ import annotations

from typing import Any

# Valeurs de l'IU distante qui ne doivent pas disparaitre sur un echec.
# `reachable` / `error` en sont volontairement exclus : ils doivent refleter la
# derniere TENTATIVE, jamais la derniere reussite.
VALUE_KEYS = (
    "last_update",
    "counters",
    "cartridges",
    "cartridge_set_counters",
    "device_status",
    "errors",
)

# Valeurs sans lesquelles les capteurs qui dependent de l'IU n'ont plus rien a
# dire : compteurs detailles (``counters``), journal de cartouches
# (``cartridges``) et table des pages portees par le jeu en place
# (``cartridge_set_counters``, d'ou sont tirees les estimations). Une page d'IU
# analysee partiellement peut se declarer « joignable » sans les fournir.
REQUIRED_VALUE_KEYS: tuple[str, ...] = (
    "counters",
    "cartridges",
    "cartridge_set_counters",
)


def merge_rui_state(
    new_data: dict[str, Any],
    last_known: dict[str, Any] | None,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Reconduit les dernieres valeurs connues apres un echec de lecture.

    ``new_data`` decrit la tentative courante (``reachable: False`` et le
    message d'erreur). Les valeurs de ``last_known`` sont reprises pour tout ce
    que ``new_data`` ne fournit pas, et le resultat porte ``stale: True`` pour
    que l'interface puisse afficher « derniere valeur connue » plutot que de
    laisser croire a une mesure fraiche.

    Trois garde-fous :

    * la reconduction n'a lieu que sur un echec **constate** (``reachable``
      explicitement faux) ou sur une lecture declaree reussie mais **incomplete**
      (``required`` fourni : les valeurs indispensables manquent) : une lecture
      reussie et complete est retournee telle quelle — on ne masque jamais ce
      que l'imprimante dit vraiment ;
    * une lecture incomplete est signalee par ``incomplete: True``, en plus de
      ``stale: True``, pour rester honnete sur la fraicheur ;
    * sans valeur connue, ``new_data`` est retourne tel quel : aucune valeur
      n'est inventee.
    """
    merged: dict[str, Any] = dict(new_data)

    reachable = new_data.get("reachable")
    failed = reachable is False
    incomplete = bool(required) and reachable is True and any(
        not merged.get(key) for key in required
    )

    if not (failed or incomplete) or not last_known:
        return merged

    carried = False
    for key in VALUE_KEYS:
        if merged.get(key):
            # La tentative courante fournit une valeur : elle gagne.
            continue
        previous = last_known.get(key)
        if previous:
            merged[key] = previous
            carried = True

    if carried:
        merged["stale"] = True
        if incomplete:
            merged["incomplete"] = True

    return merged
