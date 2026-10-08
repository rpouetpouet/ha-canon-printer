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

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the Canon Remote UI binary sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    if coordinator.data and (coordinator.data.get("rui") or {}).get("enabled"):
        async_add_entities([CanonCartridgeErrorBinarySensor(coordinator, entry)], True)


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
        self._attr_name = "Erreur cartouche"

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
        """Rattache le capteur au meme appareil que les capteurs SNMP."""
        data = self.coordinator.data or {}
        info = data.get("info", {}) or {}
        unique_id = info.get("serial_number") or self._entry.data[CONF_HOST]

        description = info.get("description", "") or ""
        location = info.get("location", "") or ""
        model = location or "Canon printer"
        if "PID:" in description:
            model = description.split("PID:")[1].split(",")[0].split(";")[0].strip()

        device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            name=data.get("device_name") or model,
            manufacturer="Canon",
            model=model,
        )
        if info.get("serial_number"):
            device_info["serial_number"] = info["serial_number"]
        return device_info
