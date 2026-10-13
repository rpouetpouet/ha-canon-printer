# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.9] - 2026-10-13

### Added
- New **Connectivity** binary sensor (`device_class: connectivity`), one per
  printer. Nothing used to answer the question "is the printer still
  answering?": when the printer stopped replying to SNMP (deep sleep,
  unplugged, changed IP) the data sensors kept showing the last known values
  — which is the intended behaviour — but nothing distinguished a fresh
  reading from a carried-over one. The sensor turns `off` on the first poll
  without an answer and exposes `last_contact`, `offline_polls`,
  `offline_error` and the Remote UI reachability, so an automation can alert
  on it.

### Fixed
- **A poll without any SNMP answer is no longer treated as a success.** The
  SNMP client returned `None` for every OID instead of raising, so the
  coordinator never reached its cached-data fallback and published empty
  values: the page counter and the display text switched to `unknown` for a
  whole poll cycle (measured: 09:56:04 → 10:01:10, one 300 s cycle) before
  coming back, with nothing pointing at the printer being unreachable. The
  client now tracks whether the agent actually answered during the poll; if
  not, the coordinator serves the last known data, marks it as offline
  (`is_online: false`, `offline_since`, `offline_error`) and leaves the cache
  untouched instead of overwriting it with empty values.
- **A successful but incomplete Remote UI reading no longer leaves holes.**
  When the Remote UI answered without the values the sensors need (detailed
  counters, cartridge log, cartridge-set page table), the detailed counters
  fell to `unknown` and the toner estimates to `unavailable` for a cycle.
  `merge_rui_state()` now carries the mandatory values over in that case too
  and flags the result as `stale` **and** `incomplete` — the failing attempt
  is still reported honestly (`reachable` reflects the attempt, not the last
  success).
- Discovery log noise: an already-configured printer re-announced over
  zeroconf (every ~8 minutes on an MF660C) is no longer logged as
  `ERROR ... Flow aborted: already_configured` with a traceback, and a SNMP
  version that answers with no data is no longer a warning — it is the
  normal case the version loop handles.

### Changed
- The toner estimate sensors now expose `rui_stale`, so a carried-over
  estimate can be told apart from a freshly computed one.

## [1.3.0] - 2026-07-15

### Added
- Optional DNS-based device naming (Issue #19): device names can now use the
  reverse-DNS short hostname or FQDN instead of the SNMP name, configurable per
  printer under the integration options. Falls back to the SNMP name when the
  lookup fails.
- Network range scanning (Issue #12): the "Add" flow now offers a menu to either
  enter a printer manually or scan a network range (CIDR, e.g. `192.168.10.0/24`)
  to discover SNMP printers, including on subnets other than Home Assistant's own.

### Changed
- Reverse-DNS lookups run in an executor and are cached to avoid per-poll work.
- Network scans probe hosts concurrently, skip already-configured printers, and
  log unreachable hosts at debug level to avoid noise.
- Refactored the model/manufacturer extraction into shared helpers used by both
  discovery paths.

## [1.2.0] - 2026-07-15

### Fixed
- Blocking `listdir`/`open` calls during setup: the SNMP engine (which loads MIBs
  from disk) is now created in an executor so it no longer blocks the Home
  Assistant event loop (Issue #20)
- Repeated `noSuchName` error spam: OIDs a printer does not expose (e.g. cover
  status) are now treated as "unsupported" and logged at debug level instead of
  being logged as connection errors on every poll (Issue #14)
- SNMPv2c/v3 missing-OID exception values (`NoSuchObject`, `NoSuchInstance`,
  `EndOfMibView`) are now handled the same way as unsupported OIDs

### Changed
- Updated `pysnmp` to 7.1.27 and aligned the pinned version between
  `manifest.json` and `requirements.txt`
- CI: bumped `actions/checkout` to v7 and added Python 3.13 to the test matrix

## [1.1.0] - 2025-10-14

### Added
- Cached values feature: Integration now remembers last known sensor values when printer is offline (Issue #3)
- Offline status indication in sensor attributes with timestamp of last successful data fetch
- Status sensor now shows "offline" state when using cached data

### Fixed
- Reduced excessive SNMP error logging when printer is offline (Issue #6)
- SNMP errors now log once as ERROR, then at WARNING level every 5 minutes to prevent log spam
- Connection recovery is properly logged when printer comes back online

## [1.0.0] - 2025-10-01

### Added
- Initial release
- SNMP v1, v2c, and v3 support
- Automatic printer discovery via Zeroconf/mDNS (a bit slow due to pulling snmp values but works)
- Manual printer configuration
- Support for major printer brands (Brother, Canon, HP, Konica Minolta, Kyocera, Lexmark, OKI, Panasonic, Ricoh, Samsung, Sharp, Xerox)
- Support for MIB and MIBII
- Wide sensor coverage:
  - Printer status sensor with attributes
  - Cover status sensor
  - Total pages sensor with color/BW breakdown
  - Toner/ink level sensors
  - Paper tray sensors
  - Waste container sensor
  - Drum unit sensors
  - Other consumable sensors
- Device information display (manufacturer, serial number, MAC address)
- Automatic web interface link detection (check if web gui reachable)
- Display text service for printer displays (make amazing automations!)
- Localization support