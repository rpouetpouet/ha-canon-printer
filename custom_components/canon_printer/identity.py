"""Identite de l'appareil — un seul endroit decide du nom, du modele et du fabricant.

Historique du defaut corrige ici : le modele etait deduit de ``sysDescr`` et, a
defaut de champ ``PID:``, **la localisation SNMP servait de nom**. Sur un Canon
dont ``sysLocation`` vaut « In the placard », l'appareil s'appelait donc
« In the placard » et **toutes** ses entites etaient prefixees
``in_the_placard_*``. La localisation est une donnee d'exploitation, jamais une
identite : elle reste une attribut du capteur d'etat, et ``sysName`` (ou a
defaut ``hrDeviceDescr``) sert de nom.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

MANUFACTURERS: tuple[str, ...] = (
    "Canon",
    "Hewlett-Packard",
    "HP",
    "Epson",
    "Brother",
    "Lexmark",
    "Samsung",
    "Xerox",
    "Kyocera",
    "Ricoh",
    "OKI",
    "Konica Minolta",
    "Sharp",
    "Panasonic",
    "Dell",
)


def _clean(text: Any) -> str:
    """Normalise les espaces d'une valeur SNMP."""
    return " ".join(str(text or "").split())


def parse_model(description: Any, name: Any = "", location: Any = "") -> str:
    """Retourne le modele de l'imprimante.

    Ordre de preference : champ ``PID:`` de ``sysDescr``, puis ``sysDescr``
    nettoye de son suffixe de langue (``"Canon MF660C Series /P"`` ->
    ``"Canon MF660C Series"``), puis ``sysName``. ``location`` n'est utilise
    qu'en tout dernier recours (imprimante totalement muette).
    """
    desc = _clean(description)
    if "PID:" in desc:
        candidate = desc.split("PID:", 1)[1]
        for separator in (",", ";"):
            candidate = candidate.split(separator)[0]
        candidate = candidate.strip()
        if candidate:
            return candidate

    # "Canon MF660C Series /P" -> on coupe le suffixe de variante "/P"
    candidate = re.split(r"\s*/\s*[A-Z]{1,2}\b", desc)[0].strip()
    candidate = candidate.split(";")[0].strip()
    if candidate:
        return candidate

    cleaned_name = _clean(name)
    if cleaned_name:
        return cleaned_name
    return _clean(location) or "Unknown Printer"


def parse_manufacturer(description: Any, default: str = "Unknown") -> str:
    """Deduit le fabricant de ``sysDescr``."""
    desc = _clean(description).lower()
    for manufacturer in MANUFACTURERS:
        if manufacturer.lower() in desc:
            return "HP" if manufacturer == "Hewlett-Packard" else manufacturer
    return default


def build_device_info(data: dict[str, Any] | None, entry: ConfigEntry) -> DeviceInfo:
    """Construit le ``DeviceInfo`` commun a toutes les plateformes.

    Mutualiser evite que les capteurs SNMP et les capteurs de l'IU distante
    divergent sur le nom de l'appareil (ce qui creait deux appareils distincts
    dans le registre).
    """
    data = data or {}
    info = data.get("info", {}) or {}
    description = info.get("description")
    host = entry.data.get(CONF_HOST)
    unique_id = info.get("serial_number") or host

    model = parse_model(description, info.get("name"), info.get("location"))
    device_info = DeviceInfo(
        identifiers={(DOMAIN, unique_id)},
        name=data.get("device_name") or model,
        manufacturer=parse_manufacturer(description),
        model=model,
    )

    if info.get("serial_number"):
        device_info["serial_number"] = info["serial_number"]
    if data.get("web_interface_available"):
        device_info["configuration_url"] = f"http://{host}"

    return device_info
