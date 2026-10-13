"""Binary sensors Canon issues de l'IU distante (Remote UI).

Expose ce que ni SNMP ni IPP ne remontent : l'etat d'erreur des cartouches
(« Impossible d'etablir la communication avec la cartouche ... »). Sur les
imprimantes Canon, une cartouche non authentique fait basculer
``printer-state-reasons`` a ``none`` en IPP alors que la machine est bien en
erreur : sans cette lecture, Home Assistant passe a cote du probleme.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN
from .identity import build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the Canon Remote UI binary sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    # Le capteur de connectivite existe toujours : c'est lui qui repond a
    # « l'imprimante repond-elle encore ? », question a laquelle rien ne
    # repondait auparavant (les capteurs de donnees, eux, continuent
    # volontairement d'afficher la derniere valeur connue).
    entities: list[BinarySensorEntity] = [
        CanonConnectivityBinarySensor(coordinator, entry)
    ]

    if coordinator.data and (coordinator.data.get("rui") or {}).get("enabled"):
        entities.append(CanonCartridgeErrorBinarySensor(coordinator, entry))

    async_add_entities(entities, True)


class CanonCartridgeErrorBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Signale une erreur de communication avec une cartouche."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        data = coordinator.data or {}
        info = data.get("info", {}) or {}
        unique_id = info.get("serial_number") or entry.data[CONF_HOST]
        self._attr_unique_id = f"{unique_id}_rui_cartridge_error"
        self._attr_translation_key = "cartridge_error"

    @property
    def available(self) -> bool:
        """Disponible des que le coordonnateur a des donnees."""
        return self.coordinator.data is not None

    @property
    def _rui(self) -> dict[str, Any]:
        """Donnees de l'IU distante (jamais None)."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("rui") or {}

    @property
    def is_on(self) -> bool:
        """Vrai quand l'imprimante signale une erreur cartouche."""
        if self.available and not self.is_printer_online:
            return False
        return bool(self._rui.get("has_error"))

    @property
    def is_printer_online(self) -> bool:
        """L'imprimante repond-elle ?"""
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("is_online", True)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Messages d'erreur en clair + fraicheur de la lecture."""
        rui = self._rui
        return {
            "errors": rui.get("errors") or [],
            "rui_reachable": rui.get("reachable"),
            "rui_last_update": rui.get("last_update"),
            "rui_error": rui.get("error"),
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Rattache le capteur au meme appareil que les capteurs SNMP.

        Passe par identity.py pour garantir un nom/modele strictement identique a
        celui des capteurs SNMP (sinon HA cree un second appareil).
        """
        return build_device_info(self.coordinator.data, self._entry)


class CanonConnectivityBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """L'imprimante repond-elle encore ?

    Aucun capteur ne repondait a cette question : quand l'agent SNMP cesse de
    repondre (veille profonde, cable debranche, IP changee), les capteurs de
    donnees continuent d'afficher la derniere valeur connue — ce qui est le
    comportement voulu — mais rien ne distinguait une mesure fraiche d'une
    valeur reconduite. Ce capteur est le guetteur : il passe a « off » des le
    premier poll sans reponse, et ses attributs datent la perte de contact.

    Etat : ``on`` = l'imprimante a repondu au dernier poll, ``off`` = aucune
    reponse (les valeurs affichees ailleurs sont reconduites).
    """

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connectivity"

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the connectivity binary sensor."""
        super().__init__(coordinator)
        self._entry = entry
        data = coordinator.data or {}
        info = data.get("info", {}) or {}
        unique_id = info.get("serial_number") or entry.data[CONF_HOST]
        self._attr_unique_id = f"{unique_id}_connectivity"

    @property
    def available(self) -> bool:
        """Disponible des que le coordonnateur a des donnees.

        Jamais indisponible a cause de la perte de contact : c'est justement
        l'information qu'il porte.
        """
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        """Vrai si l'imprimante a repondu au dernier poll."""
        if not self.coordinator.data:
            return False
        return bool(self.coordinator.data.get("is_online", True))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Datation de la perte de contact + etat de l'IU distante."""
        data = self.coordinator.data or {}
        rui = data.get("rui") or {}
        return {
            # Horodatage du dernier poll REUSSI (absent = jamais joignable)
            "last_contact": data.get("offline_since"),
            "offline_polls": data.get("offline_polls", 0),
            "offline_error": data.get("offline_error"),
            "snmp_consecutive_failures": data.get("consecutive_failures", 0),
            "rui_reachable": rui.get("reachable"),
            "rui_stale": bool(rui.get("stale")),
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Rattache le capteur au meme appareil que les capteurs SNMP."""
        return build_device_info(self.coordinator.data, self._entry)
