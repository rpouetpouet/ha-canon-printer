"""The SNMP Printer integration."""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime, timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .canon_rui import CanonRuiAuthError, CanonRuiClient, CanonRuiError
from .identity import parse_model
from .const import (
    CONF_NAME_SOURCE,
    CONF_RUI_ADMIN,
    CONF_RUI_ENABLED,
    CONF_RUI_PASSWORD,
    CONF_RUI_SYSTEM_MANAGER_ID,
    CONF_UPDATE_INTERVAL,
    DEFAULT_NAME_SOURCE,
    DEFAULT_RUI_ADMIN,
    DEFAULT_RUI_ENABLED,
    DEFAULT_RUI_SYSTEM_MANAGER_ID,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    NAME_SOURCE_DNS_FQDN,
    NAME_SOURCE_DNS_HOSTNAME,
)
from .snmp_client import SNMPClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]
STORAGE_VERSION = 1
STORAGE_KEY = "canon_printer_cached_data"


async def async_resolve_device_name(
    hass: HomeAssistant, host: str, name_source: str
) -> str | None:
    """Resolve a device name from DNS (reverse lookup).

    Returns the short hostname or FQDN depending on ``name_source``. Returns
    ``None`` when DNS naming is not requested or the lookup fails, so callers
    can fall back to the SNMP-based name (issue #19).
    """
    if name_source not in (NAME_SOURCE_DNS_HOSTNAME, NAME_SOURCE_DNS_FQDN):
        return None

    try:
        result = await hass.async_add_executor_job(socket.gethostbyaddr, host)
        fqdn = result[0]
    except (socket.herror, socket.gaierror, OSError) as err:
        _LOGGER.debug("Reverse DNS lookup failed for %s: %s", host, err)
        return None

    if not fqdn:
        return None

    if name_source == NAME_SOURCE_DNS_HOSTNAME:
        return fqdn.split(".")[0]
    return fqdn


def _async_maybe_fix_title(
    hass: HomeAssistant, entry: ConfigEntry, info: dict[str, Any]
) -> None:
    """Remplace un titre d'entree auto-genere par le modele de l'imprimante.

    Defaut historique : sans champ ``PID:`` dans ``sysDescr``, la localisation
    SNMP servait de nom, ce qui donnait une entree « In the placard ». On ne
    touche jamais a un titre choisi par l'utilisateur : seuls la localisation,
    l'adresse IP ou un titre vide sont remplaces.
    """
    current = (entry.title or "").strip()
    location = " ".join(str(info.get("location") or "").split())
    host = entry.data.get(CONF_HOST, "")
    if current not in (location, host, ""):
        return
    model = parse_model(info.get("description"), info.get("name"), info.get("location"))
    if model and model != "Unknown Printer" and model != current:
        _LOGGER.info("Titre de l'entree corrige : %s -> %s", current, model)
        hass.config_entries.async_update_entry(entry, title=model)


async def check_web_interface(host: str, hass: HomeAssistant) -> bool:
    """Check if the printer has a web interface available."""
    session = async_get_clientsession(hass)

    # Try HTTP first
    try:
        async with asyncio.timeout(3):
            async with session.get(f"http://{host}", allow_redirects=True) as response:
                if (
                    response.status < 500
                ):  # Any response below 500 means web interface exists
                    return True
    except Exception:
        pass

    # Try HTTPS
    try:
        async with asyncio.timeout(3):
            async with session.get(
                f"https://{host}", allow_redirects=True, ssl=False
            ) as response:
                if response.status < 500:
                    return True
    except Exception:
        pass

    return False


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up SNMP Printer from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create SNMP client
    snmp_client = SNMPClient(
        host=entry.data[CONF_HOST],
        port=entry.data.get("port", 161),
        snmp_version=entry.data.get("snmp_version", "2c"),
        community=entry.data.get("community", "public"),
        username=entry.data.get("username"),
        auth_protocol=entry.data.get("auth_protocol"),
        auth_key=entry.data.get("auth_key"),
        priv_protocol=entry.data.get("priv_protocol"),
        priv_key=entry.data.get("priv_key"),
    )

    # Get update interval from config or options
    update_interval = entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
    )

    # --- Enrichissement « IU distante » (Canon Remote UI) ---------------------
    # Apporte ce que SNMP et IPP n'exposent pas : messages d'erreur cartouche en
    # clair, compteurs detailles et journal des cartouches.
    rui_enabled = entry.options.get(
        CONF_RUI_ENABLED, entry.data.get(CONF_RUI_ENABLED, DEFAULT_RUI_ENABLED)
    )
    rui_password = entry.options.get(
        CONF_RUI_PASSWORD, entry.data.get(CONF_RUI_PASSWORD, "")
    )
    rui_admin = entry.options.get(
        CONF_RUI_ADMIN, entry.data.get(CONF_RUI_ADMIN, DEFAULT_RUI_ADMIN)
    )
    rui_system_manager_id = entry.options.get(
        CONF_RUI_SYSTEM_MANAGER_ID,
        entry.data.get(CONF_RUI_SYSTEM_MANAGER_ID, DEFAULT_RUI_SYSTEM_MANAGER_ID),
    )

    rui_client: CanonRuiClient | None = None
    if rui_enabled and rui_password:
        # ⚠️ aiohttp refuse par defaut les cookies d'un hote exprime en IP :
        # sans une jar "unsafe", la session de l'IU distante n'est jamais
        # conservee et toutes les pages repondent « session expiree ».
        rui_session = async_create_clientsession(
            hass, cookie_jar=aiohttp.CookieJar(unsafe=True)
        )
        rui_client = CanonRuiClient(
            rui_session,
            host=entry.data[CONF_HOST],
            password=rui_password,
            admin=rui_admin,
            system_manager_id=rui_system_manager_id,
        )
        _LOGGER.debug("IU distante activee pour %s", entry.data[CONF_HOST])

    # Create storage for cached data
    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")

    # Load cached data
    cached_data = await store.async_load() or {}

    # Device naming preference (issue #19). Reverse DNS results are cached so we
    # don't perform a lookup on every poll.
    name_source = entry.options.get(
        CONF_NAME_SOURCE,
        entry.data.get(CONF_NAME_SOURCE, DEFAULT_NAME_SOURCE),
    )
    dns_name_cache: dict[str, str | None] = {}

    async def async_get_device_name() -> str | None:
        """Return the DNS-based device name, caching the lookup."""
        host = entry.data[CONF_HOST]
        cache_key = f"{host}:{name_source}"
        if cache_key not in dns_name_cache:
            dns_name_cache[cache_key] = await async_resolve_device_name(
                hass, host, name_source
            )
        return dns_name_cache[cache_key]

    # Create coordinator
    async def async_update_data():
        """Fetch data from SNMP printer."""
        try:
            system_info = await snmp_client.get_system_info()
            device_info = await snmp_client.get_device_info()

            # Canon Remote UI : echec tolere, ne doit jamais priver des donnees SNMP
            rui_data: dict = {"enabled": rui_client is not None}
            if rui_client is not None:
                try:
                    rui_data = {
                        "enabled": True,
                        **(await rui_client.async_fetch()).as_dict(),
                    }
                except (CanonRuiError, CanonRuiAuthError) as err:
                    _LOGGER.warning("IU distante Canon indisponible: %s", err)
                    rui_data = {
                        "enabled": True,
                        "reachable": False,
                        "error": str(err),
                    }

            info = {**system_info, **device_info}
            _async_maybe_fix_title(hass, entry, info)

            data = {
                "info": info,
                "status": device_info,
                "device_name": await async_get_device_name(),
                "cover_status": {"state": await snmp_client.get_cover_status()},
                "page_count": device_info.get(
                    "page_counts", {"total": device_info.get("page_count")}
                ),
                "supplies": await snmp_client.get_supplies(),
                "input_trays": await snmp_client.get_input_trays(),
                "display_text": await snmp_client.get_display_text(),
                "errors": await snmp_client.get_printer_errors(),
                "rui": rui_data,
                "web_interface_available": await check_web_interface(
                    entry.data[CONF_HOST], hass
                ),
            }

            # Save successful data to cache with timestamp
            cache_data = {
                "data": data,
                "timestamp": datetime.now().isoformat(),
                "host": entry.data[CONF_HOST],
            }
            await store.async_save(cache_data)

            # Mark as online
            data["is_online"] = True

            return data
        except Exception as err:
            # Check if this is a connection-related error
            error_msg = str(err).lower()
            is_connection_error = any(
                keyword in error_msg
                for keyword in [
                    "timeout",
                    "unreachable",
                    "no route",
                    "connection",
                    "network",
                    "host",
                    "refused",
                    "failed",
                    "no response",
                ]
            )

            # If we have cached data and this is a connection issue, return cached data
            if cached_data.get("data") and is_connection_error:
                _LOGGER.warning(
                    "Printer %s is offline (%s), using cached data from %s",
                    entry.data[CONF_HOST],
                    err,
                    cached_data.get("timestamp", "unknown"),
                )

                cached_printer_data = cached_data["data"].copy()
                cached_printer_data["is_online"] = False
                cached_printer_data["offline_since"] = cached_data.get("timestamp")

                return cached_printer_data

            # For other errors or when we don't have cached data, re-raise the error
            raise UpdateFailed(f"Error fetching printer data: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"canon_printer_{entry.data[CONF_HOST]}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=update_interval),
    )

    # Fetch initial data (non-blocking, allows offline printers to complete setup)
    # This prevents blocking Home Assistant boot when printer is unreachable
    await coordinator.async_refresh()

    # Store coordinator, client, and storage
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": snmp_client,
        "store": store,
    }

    # Forward entry setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register update listener for options
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
