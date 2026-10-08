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

| Entity | Category | What it adds |
|---|---|---|
| `binary_sensor.<printer>_cartridge_error` | — (main) | Cartridge communication error, device class `problem`. Attributes: `errors` (clear-text messages), `rui_reachable`, `rui_last_update` |
| `sensor.<printer>_detailed_counters` | Diagnostic | Total print counter as state, all Remote UI counters in the `counters` attribute |
| `sensor.<printer>_cartridge_log` | Diagnostic | Number of cartridges actually reporting data, full log in the `cartridges` attribute (`serial`, `type`, `capacity`, `first_used`, `first_level`, `last_used`, `last_level`, `genuine`) |
| `sensor.<printer>_<colour>_estimated` | — (main) | **Estimated** remaining toner level (×4: black, cyan, magenta, yellow) — see below |

`genuine: false` = the printer reads **no serial number** for that cartridge
(shown as `----------` in the web UI) and the level stays `-%`. That is the
signature of a non-genuine / non-reporting chip — the reason a toner level can
never appear for it.

### How entities are organised

- **Measured vs estimated is explicit in the name**: `Black toner` (SNMP value,
  normally a percentage) vs `Black toner (estimated)` (computed). There is no
  ambiguity about which one can be trusted as a measurement.
- **`Diagnostic` category** for everything that is not actionable in a
  dashboard or automation: page count, detailed counters, cartridge log,
  errors count, display text, cover status. Toner levels, estimates, trays and
  the cartridge-error binary sensor stay in the main category.
- **Device identity** is centralised in `identity.py`. The model comes from
  `sysDescr` (`PID:` field, otherwise the description stripped of its variant
  suffix) or `sysName` — **never from `sysLocation`**. Earlier versions used the
  location as a fallback, so a printer whose `sysLocation` was
  *"In the placard"* produced a device named after its shelf and every entity
  prefixed `in_the_placard_*`.

## Estimating the toner level when the chip reports nothing

A non-reporting chip cannot be fixed in software (see the table above), but the
printer still exposes enough to **estimate** a level — and with such cartridges
**no "low toner" warning will ever be emitted**, so an estimate is the only
early signal available.

The Remote UI cartridge log ends with an undocumented counter table:

```
C2:  00004  0000000819
C3:  00000  0000000000
C9:  00000  0000000000
```

The **second field is the number of pages carried by the cartridge set** of
that type. This was **validated empirically**, not assumed: printing 3 pages
moved it from 816 to 819, exactly like `prtMarkerLifeCount` (1234 → 1237) and
the Remote UI print counter (1205 → 1208), while the colour counter stayed put
(the 3 pages were black and white).

Estimation model (`estimation.py`), with its assumptions stated in the entity
attributes:

1. every printed page consumes black toner;
2. only colour pages consume colour toner;
3. the colour/mono split of the current set is unknown (Remote UI counters are
   lifetime totals), so the lifetime mix is used as the approximation;
4. reference yields come from the manufacturer (ISO/IEC 19798, 5 % coverage),
   configurable in the integration options — e.g. 3 500 pages (black) and
   2 500 pages (colour) for a high-capacity Canon 075.

The sensors report **nothing** (state `unavailable`) as soon as one of the
required inputs is missing: no invented estimate.

### Validating the counter on another model

1. Read `prtMarkerLifeCount`, the four Remote UI counters and the `C2:`
   table.
2. Print a few pages, preferably black and white only.
3. Read everything again: the second field of the current set line must have
   moved by exactly the number of pages printed. If it did, the estimate has a
   real baseline; if not, the model does not apply and no estimate is exposed.

## Brand images

`custom_components/canon_printer/brand/` ships the integration icon and logo
(light and dark variants, normal and `@2x`), which Home Assistant 2026.3+
serves locally via `/api/brands/integration/canon_printer/icon.png`. The images
are generated by `assets/generate_brand_icons.py` so they can be reproduced
exactly. Note that a **full Home Assistant restart** is required for brand
files to be picked up (the flag is cached per process), and the manifest
version must be bumped for HACS to deliver them.

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
