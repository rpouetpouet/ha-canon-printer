# Canon Remote UI enrichment (fork of DSorlov/snmp_printer)

This fork keeps the whole generic SNMP printer support of
[DSorlov/snmp_printer](https://github.com/DSorlov/snmp_printer) and **adds a
second, Canon-specific data source: the printer's own web interface
("IU distante" / Remote UI)**.

The reason is simple: on Canon i-SENSYS / imageCLASS MF and LBP printers, some
information **does not exist in SNMP or IPP at all**.

## Why SNMP/IPP are not enough on Canon

| Information | SNMP | IPP | Remote UI |
|---|---|---|---|
| Printer state, page count, trays, serial | ✅ | ✅ | ✅ |
| Toner level | ⚠️ `-2` (unknown) with non-genuine chips | ⚠️ `-1` (unknown) | ❌ not reported either |
| **Cartridge communication error** | ❌ (`printer-state-reasons = none`) | ❌ (same) | ✅ **explicit message** |
| **Detailed counters** (b/w small, colour, scans, prints) | ❌ (total only) | ❌ | ✅ |
| **Cartridge log** (install date, past levels, serial) | ❌ | ❌ | ✅ |

A Canon printer can display *"Impossible d'établir la communication avec la
cartouche cyan"* while both `printer-state-reasons` (IPP) and the standard
Printer MIB report **no error at all**. Without the Remote UI, Home Assistant
simply cannot see the problem.

## Added entities (only when the enrichment is enabled)

- `binary_sensor.<printer>_erreur_cartouche` — device class `problem`.
  Attributes: `errors` (clear-text messages), `rui_reachable`,
  `rui_last_update`.
- `sensor.<printer>_compteurs_detailles` — total print counter as state,
  all Remote UI counters in the `counters` attribute.
- `sensor.<printer>_cartouches_journal` — number of cartridges actually
  reporting data, full cartridge log in the `cartridges` attribute
  (`serial`, `type`, `capacity`, `first_used`, `first_level`, `last_used`,
  `last_level`, `genuine`).

`genuine: false` = the printer reads **no serial number** for that cartridge
(shown as `----------` in the web UI) and the level stays `-%`. That is the
signature of a non-genuine / non-reporting chip — the reason a toner level can
never appear for it.

## Configuration

1. Add the integration, point it at the printer IP (it is also discovered via
   Zeroconf `_ipp._tcp` / `_ipps._tcp` / `_printer._tcp`).
2. **SNMP version: choose `1`.** Canon firmware commonly answers **only
   SNMPv1**; `2c` silently times out. Community is usually `public`
   (the printer's Remote UI shows it under *Réglages réseau → Réglages SNMP*).
3. Open the integration **Configure** dialog and enable the Remote UI
   enrichment:
   - `Enable Canon Remote UI enrichment` → on
   - `IU distante password` → the password of the printer's web interface
     (general-user mode needs the password only; on the tested MF660C, admin
     mode works with the password alone and an empty System Manager ID)
   - `Admin mode` → off is enough for errors/counters/log
   - `System Manager ID` → only if your printer requires it

## Implementation notes / pitfalls

- **`aiohttp` refuses cookies from an IP host by default.** Without
  `aiohttp.CookieJar(unsafe=True)` the Remote UI session is never kept and
  every page answers *"La session a expiré"*. The integration creates its own
  session with an unsafe jar for this reason.
- **The Remote UI rate-limits.** Probes too close together make the printer
  stop answering for a while (paths then look `filtered` to `nmap`). The
  client spaces its requests (2 s) and failures are tolerated: a Remote UI
  error never removes the SNMP data.
- **The cartridge log shows one colour at a time** (a `<select>` + a POST to
  `/cgi/cartridge_log.cgi` with a per-session `iToken`), so four pages are
  fetched per poll.
- Poll interval: Remote UI pages are only fetched at the coordinator's normal
  interval; use a longer interval (e.g. 300 s) if you want to be gentle.

## Credits

Base integration © [DSorlov](https://github.com/DSorlov) (MIT). This fork adds
the Canon Remote UI data source. MIT licence, see `LICENSE`.
