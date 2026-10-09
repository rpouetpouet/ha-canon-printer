"""Support for SNMP Printer sensors."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    CONF_YIELD_BLACK,
    CONF_YIELD_COLOR,
    DEFAULT_YIELD_BLACK,
    DEFAULT_YIELD_COLOR,
    DOMAIN,
    PRINTER_STATUS,
)
from .estimation import TONER_COLORS, TonerEstimate, estimate_cartridges
from .identity import build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SNMP Printer sensors based on a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []

    # Add main status sensor
    entities.append(PrinterStatusSensor(coordinator, entry))

    # Add cover status sensor
    entities.append(PrinterCoverStatusSensor(coordinator, entry))

    # Add page count sensor
    entities.append(PrinterPageCountSensor(coordinator, entry))

    # Add error sensor
    entities.append(PrinterErrorSensor(coordinator, entry))

    # Add display text sensor
    entities.append(PrinterDisplayTextSensor(coordinator, entry))

    # Add supply sensors (toner, ink, drums, etc.)
    if coordinator.data and "supplies" in coordinator.data:
        for supply in coordinator.data["supplies"]:
            entities.append(PrinterSupplySensor(coordinator, entry, supply))

    # Add tray sensors
    if coordinator.data and "input_trays" in coordinator.data:
        for tray in coordinator.data["input_trays"]:
            entities.append(PrinterTraySensor(coordinator, entry, tray))

    # Canon Remote UI sensors (enrichissement optionnel)
    if coordinator.data and coordinator.data.get("rui", {}).get("enabled"):
        entities.append(PrinterRuiCountersSensor(coordinator, entry))
        entities.append(PrinterRuiCartridgeLogSensor(coordinator, entry))
        # Estimation du niveau : la puce d'une cartouche non d'origine ne
        # transmet rien (SNMP -2, IPP -1, IU distante -%). On estime donc le
        # reste a partir des compteurs de la machine (cf. estimation.py).
        for color in TONER_COLORS:
            entities.append(PrinterTonerEstimateSensor(coordinator, entry, color))

    async_add_entities(entities, True)


class PrinterSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for printer sensors."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_has_entity_name = True

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        # Entity is available if we have data (either live or cached)
        return self.coordinator.data is not None

    @property
    def is_printer_online(self) -> bool:
        """Check if printer is currently online."""
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("is_online", True)

    @property
    def device_info(self) -> DeviceInfo:
        """Informations de l'appareil (nom, modele, fabricant).

        Delegue a identity.py : le nom ne doit JAMAIS etre deduit de la
        localisation SNMP, sans quoi l'appareil porte le nom de son emplacement
        et toutes ses entites en heritent le prefixe.
        """
        return build_device_info(self.coordinator.data, self._entry)


class PrinterRuiCountersSensor(PrinterSensorBase):
    """Compteurs detailles lus dans l'IU distante.

    SNMP n'expose que le compteur de pages total ; l'IU distante donne la
    ventilation (noir et blanc petit format, quadrichromie, lectures, ...).
    """

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_rui_counters"
        self._attr_translation_key = "rui_counters"
        self._attr_icon = "mdi:counter"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def _rui(self) -> dict[str, Any]:
        """Donnees de l'IU distante (jamais None)."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("rui") or {}

    @property
    def native_value(self) -> int | None:
        """Retourne le compteur d'impressions total si disponible."""
        counters = self._rui.get("counters") or {}
        for label, value in counters.items():
            if "impression" in label.lower():
                return int(value)
        if not counters:
            return None
        return sum(int(v) for v in counters.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Tous les compteurs + horodatage de l'IU distante."""
        rui = self._rui
        return {
            "counters": rui.get("counters") or {},
            "rui_last_update": rui.get("last_update"),
            "rui_reachable": rui.get("reachable"),
            # true = valeurs reconduites apres un echec de lecture de l'IU
            "rui_stale": bool(rui.get("stale")),
        }


class PrinterRuiCartridgeLogSensor(PrinterSensorBase):
    """Journal des cartouches (date d'installation, niveaux passes).

    Permet de savoir si une cartouche remonte son niveau : une cartouche
    « no-name » apparait sans numero de serie et avec un niveau « -% ».
    """

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_rui_cartridges"
        self._attr_translation_key = "rui_cartridge_log"
        self._attr_icon = "mdi:package-variant-closed"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def _rui(self) -> dict[str, Any]:
        """Donnees de l'IU distante (jamais None)."""
        if not self.coordinator.data:
            return {}
        return self.coordinator.data.get("rui") or {}

    @property
    def _records(self) -> list[dict[str, Any]]:
        """Tous les enregistrements de cartouches, toutes couleurs."""
        out: list[dict[str, Any]] = []
        for color, recs in (self._rui.get("cartridges") or {}).items():
            for rec in recs:
                out.append({"color": color, **rec})
        return out

    @property
    def native_value(self) -> int | None:
        """Nombre de cartouches dont le niveau est remonte."""
        records = [r for r in self._records if r.get("genuine")]
        return len(records) if self._records else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Journal complet + comptage origine / non identifiee."""
        records = self._records
        return {
            "cartridges": records,
            "genuine_count": sum(1 for r in records if r.get("genuine")),
            "unidentified_count": sum(1 for r in records if not r.get("genuine")),
        }


class PrinterTonerEstimateSensor(PrinterSensorBase):
    """Niveau estime d'une cartouche dont la puce ne repond pas.

    Le calcul (cf. ``estimation.py``) part du nombre de pages portees par le jeu
    de cartouches en place — lu dans la table de compteurs du journal de l'IU
    distante — et du rendement constructeur reglable dans les options. C'est une
    ESTIMATION : elle n'a de sens que parce que la mesure directe est
    impossible (SNMP ``-2``, IPP ``-1``, IU distante ``-%``).
    """

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        color: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._color = color
        self._attr_translation_key = f"estimated_{color}"
        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_estimated_{color}"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:water-percent"

    @property
    def _estimate(self) -> TonerEstimate | None:
        """Estimation courante (None tant que les donnees necessaires manquent)."""
        rui = (self.coordinator.data or {}).get("rui") or {}
        options = self._entry.options or {}
        estimates = estimate_cartridges(
            rui,
            int(options.get(CONF_YIELD_BLACK, DEFAULT_YIELD_BLACK)),
            int(options.get(CONF_YIELD_COLOR, DEFAULT_YIELD_COLOR)),
        )
        return estimates.get(self._color)

    @property
    def available(self) -> bool:
        """Pas d'estimation inventee : indisponible si les donnees manquent."""
        return super().available and self._estimate is not None

    @property
    def native_value(self) -> float | None:
        """Pourcentage restant estime."""
        estimate = self._estimate
        return estimate.remaining_percent if estimate else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Detail du calcul, pour pouvoir le discuter chiffre en main."""
        estimate = self._estimate
        if estimate is None:
            return {}
        rui = (self.coordinator.data or {}).get("rui") or {}
        attributes = estimate.as_attributes()
        attributes["pages_with_current_set"] = rui.get("cartridge_set_pages")
        attributes["source"] = "compteurs imprimante + journal de cartouche (IU distante)"
        return attributes


class PrinterStatusSensor(PrinterSensorBase):
    """Representation of a printer status sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "status"
        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_status"
        self._attr_icon = "mdi:printer"
        self._attr_device_class = SensorDeviceClass.ENUM
        # La valeur vient de hrDeviceStatus (RFC 2790) : elle peut valoir
        # n'importe lequel de ces etats. 'other' (1) est renvoye par plusieurs
        # Canon et n'etait pas declare -> l'entite etait refusee par HA avec
        # "provides state value 'other', which is not in the list of options".
        self._attr_options = [
            "other",
            "unknown",
            "online",
            "warning",
            "testing",
            "down",
            "idle",
            "printing",
            "warming_up",
            "offline",
        ]

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return "unknown"

        # If printer is offline, return offline status
        if not self.is_printer_online:
            return "offline"

        status = self.coordinator.data.get("status", {})
        return status.get("state", "unknown")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}

        info = self.coordinator.data.get("info", {})
        status = self.coordinator.data.get("status", {})

        attributes = {
            "uptime": info.get("uptime"),
            "contact": info.get("contact"),
            "location": info.get("location"),
            "serial_number": info.get("serial_number"),
            "description": info.get("description"),
            "state_source": info.get("state_source"),
            "device_status_raw": info.get("device_status_raw"),
            "printer_status_raw": info.get("printer_status_raw"),
        }

        # Traduction lisible de hrPrinterStatus (activite) : la valeur brute
        # « 3 » ne dit rien, « idle » si. L'etat du capteur reste, lui, la
        # gravite (hrDeviceStatus : online / warning / down).
        raw_printer_status = info.get("printer_status_raw")
        if raw_printer_status is not None:
            attributes["printer_activity"] = PRINTER_STATUS.get(
                int(raw_printer_status), "unknown"
            )

        # Statut affiche par l'imprimante elle-meme (IU distante), plus parlant
        # que le code SNMP : « Imprimante : Une erreur s'est produite. »
        rui_status = (self.coordinator.data.get("rui") or {}).get("device_status") or {}
        if rui_status.get("printer"):
            attributes["rui_printer_state"] = rui_status["printer"]
        if rui_status.get("scanner"):
            attributes["rui_scanner_state"] = rui_status["scanner"]

        # Add offline information if using cached data
        if not self.is_printer_online:
            attributes["using_cached_data"] = True
            offline_since = self.coordinator.data.get("offline_since")
            if offline_since:
                attributes["offline_since"] = offline_since

        # Remove None values
        return {k: v for k, v in attributes.items() if v is not None}


class PrinterCoverStatusSensor(PrinterSensorBase):
    """Representation of a printer cover status sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "cover_status"
        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_cover_status"
        self._attr_icon = "mdi:printer-3d-nozzle-alert"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return if the entity should be enabled when first added."""
        # Disable by default if no cover data or state is unknown
        if not self.coordinator.data:
            return False
        cover_status = self.coordinator.data.get("cover_status", {})
        state = cover_status.get("state", "unknown")
        # Enable only if we have a valid state (not unknown)
        return state != "unknown" and state != ""

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return "unknown"

        cover_status = self.coordinator.data.get("cover_status", {})
        return cover_status.get("state", "unknown")


class PrinterPageCountSensor(PrinterSensorBase):
    """Representation of a printer page count sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "page_count"
        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_page_count"
        self._attr_icon = "mdi:counter"
        self._attr_native_unit_of_measurement = "pages"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return None

        page_count = self.coordinator.data.get("page_count", {})
        return page_count.get("total")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}

        page_count = self.coordinator.data.get("page_count", {})
        attrs = {}

        if page_count.get("color") is not None:
            attrs["color_pages"] = page_count.get("color")

        if page_count.get("black_and_white") is not None:
            attrs["black_and_white_pages"] = page_count.get("black_and_white")

        # Add offline information if using cached data
        if not self.is_printer_online:
            attrs["using_cached_data"] = True
            offline_since = self.coordinator.data.get("offline_since")
            if offline_since:
                attrs["last_updated"] = offline_since

        return attrs


class PrinterSupplySensor(PrinterSensorBase):
    """Representation of a printer supply sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        supply: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._supply = supply

        # Set translation key based on color (lowercase with underscores)
        color = supply.get("color", "")
        if color and color != "Unknown":
            color_key = color.lower().replace(" ", "_")
            self._attr_translation_key = color_key
        else:
            # Fallback to description for non-standard supplies
            self._attr_name = supply.get("description", "Supply")

        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_supply_{supply.get('index')}"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT

        # Set icon based on color - use droplets for all ink/toner
        if color in [
            "Black",
            "Cyan",
            "Magenta",
            "Yellow",
            "Gray",
            "Grey",
            "Light Cyan",
            "Light Magenta",
            "Photo",
        ]:
            self._attr_icon = "mdi:water"
        else:
            # For unknown colors, check supply type
            supply_type = supply.get("type", "").lower()
            if "toner" in supply_type or "ink" in supply_type:
                self._attr_icon = "mdi:water"
            elif "drum" in supply_type or "image" in supply_type:
                self._attr_icon = "mdi:circle-outline"
            else:
                self._attr_icon = "mdi:package-variant"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """N'activer le capteur que s'il peut reellement fournir une valeur.

        Une cartouche dont la puce ne repond pas (SNMP ``-2``, IPP ``-1``) reste
        indefiniment a « unknown » : l'entite est alors creee DESACTIVEE pour ne
        pas polluer l'interface, et l'utilisateur peut la reactiver d'un clic
        s'il installe des cartouches d'origine. Le niveau reste suivi par les
        capteurs d'estimation, qui n'ont pas besoin de la puce.
        """
        if not self.coordinator.data or "supplies" not in self.coordinator.data:
            return True
        for supply in self.coordinator.data["supplies"]:
            if supply.get("index") == self._supply.get("index"):
                return supply.get("percentage") is not None
        return True

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        # Update supply data from coordinator
        if not self.coordinator.data or "supplies" not in self.coordinator.data:
            return None

        for supply in self.coordinator.data["supplies"]:
            if supply.get("index") == self._supply.get("index"):
                return supply.get("percentage")

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data or "supplies" not in self.coordinator.data:
            return {}

        for supply in self.coordinator.data["supplies"]:
            if supply.get("index") == self._supply.get("index"):
                attributes = {
                    "type": supply.get("type"),
                    "color": supply.get("color"),
                    "description": supply.get("description"),
                }

                # Add offline information if using cached data
                if not self.is_printer_online:
                    attributes["using_cached_data"] = True
                    offline_since = self.coordinator.data.get("offline_since")
                    if offline_since:
                        attributes["last_updated"] = offline_since

                # Add RGB color code for UI customization
                color = supply.get("color", "")
                if color == "Black":
                    attributes["rgb_color"] = [0, 0, 0]
                elif color == "Cyan":
                    attributes["rgb_color"] = [0, 255, 255]
                elif color == "Magenta":
                    attributes["rgb_color"] = [255, 0, 255]
                elif color == "Yellow":
                    attributes["rgb_color"] = [255, 255, 0]
                elif color == "Gray" or color == "Grey":
                    attributes["rgb_color"] = [128, 128, 128]
                elif color == "Light Cyan":
                    attributes["rgb_color"] = [128, 255, 255]
                elif color == "Light Magenta":
                    attributes["rgb_color"] = [255, 128, 255]
                elif color == "Photo":
                    attributes["rgb_color"] = [128, 128, 255]

                return attributes

        return {}


class PrinterTraySensor(PrinterSensorBase):
    """Representation of a printer tray sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
        tray: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._tray = tray

        # Extract tray name from description (e.g., "Tray 1", "MP Tray")
        description = tray.get("description", "")
        tray_name = description if description else f"Tray {tray.get('index', '')}"

        # Set translation key for standard trays (tray_1, tray_2, etc.)
        if "Tray" in tray_name and any(char.isdigit() for char in tray_name):
            # Extract number from tray name
            tray_num = "".join(filter(str.isdigit, tray_name))
            if tray_num:
                self._attr_translation_key = f"tray_{tray_num}"
        else:
            # For non-standard trays (e.g., "MP Tray"), use explicit name
            self._attr_name = tray_name

        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_tray_{tray.get('index')}"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_icon = "mdi:tray"
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return if the entity should be enabled when first added."""
        # Only enable tray sensors that have valid percentage data
        # Trays without max_capacity or current_level won't have percentage
        return self._tray.get("percentage") is not None

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        # Update tray data from coordinator
        if not self.coordinator.data or "input_trays" not in self.coordinator.data:
            return None

        for tray in self.coordinator.data["input_trays"]:
            if tray.get("index") == self._tray.get("index"):
                return tray.get("percentage")

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data or "input_trays" not in self.coordinator.data:
            return {}

        for tray in self.coordinator.data["input_trays"]:
            if tray.get("index") == self._tray.get("index"):
                attributes = {
                    "status": tray.get("status"),
                    "media_name": tray.get("media_name"),
                    "max_capacity": tray.get("max_capacity"),
                    "current_level": tray.get("current_level"),
                }

                # Add offline information if using cached data
                if not self.is_printer_online:
                    attributes["using_cached_data"] = True
                    offline_since = self.coordinator.data.get("offline_since")
                    if offline_since:
                        attributes["last_updated"] = offline_since

                return attributes

        return {}


class PrinterErrorSensor(PrinterSensorBase):
    """Representation of a printer error sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "errors"
        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_errors"
        self._attr_icon = "mdi:alert"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return "none"

        errors = self.coordinator.data.get("errors")
        return errors if errors else "none"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return if the entity should be enabled when first added."""
        # Always enable error sensor
        return True


class PrinterDisplayTextSensor(PrinterSensorBase):
    """Representation of a printer display text sensor."""

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry)
        self._attr_translation_key = "display"
        unique_id = (
            self.coordinator.data.get("info", {}).get(
                "serial_number", entry.data[CONF_HOST]
            )
            if self.coordinator.data
            else entry.data[CONF_HOST]
        )
        self._attr_unique_id = f"{unique_id}_display"
        self._attr_icon = "mdi:text-box"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return "unknown"

        display_text = self.coordinator.data.get("display_text")
        return display_text if display_text else "unknown"

    @property
    def entity_registry_enabled_default(self) -> bool:
        """Return if the entity should be enabled when first added."""
        # Disable by default if no display text
        if not self.coordinator.data:
            return False
        display_text = self.coordinator.data.get("display_text")
        return display_text is not None and display_text != ""
