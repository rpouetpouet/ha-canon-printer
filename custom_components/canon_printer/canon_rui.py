"""Client async pour l'IU distante Canon (Remote UI) — recupère ce que SNMP/IPP
n'exposent pas : erreurs cartouche en clair, compteurs détaillés, journal des
cartouches (date d'installation, niveaux passés).

Fonctionne sur les imprimantes Canon i-SENSYS/imageCLASS MF/LBP exposant
/portal_top.html. Auth : mode « utilisateur général » (mot de passe seul) ou
« administrateur » (System Manager ID vide + PIN sur ce modèle).

Stdlib + aiohttp uniquement (aiohttp est fourni par Home Assistant).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 20
# L'IU distante Canon limite les requetes rapprochees : on espace les appels.
REQUEST_SPACING = 2.0


class CanonRuiError(Exception):
    """Erreur generique de dialogue avec l'IU distante."""


class CanonRuiAuthError(CanonRuiError):
    """Identifiants refuses."""


def _clean(html: str) -> str:
    """Retire scripts/styles/balises et normalise les espaces."""
    html = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</(?:tr|div|p|li|h\d)>", "\n", html, flags=re.I)
    html = re.sub(r"</t[dh]>", "\t", html, flags=re.I)
    text = re.sub(r"<[^>]+>", "", html)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&#39;", "'")
    text = text.replace("&gt;", ">").replace("&lt;", "<").replace("&quot;", '"')
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _rows(html: str) -> list[list[str]]:
    """Retourne les lignes de tableau : liste de listes de cellules nettoyees."""
    out: list[list[str]] = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)
        clean = []
        for c in cells:
            txt = _clean(c)
            txt = " ".join(txt.split())
            clean.append(txt)
        if any(clean):
            out.append(clean)
    return out


def _parse_when(text: str | None) -> datetime | None:
    """Convertit « 10/05 2026 15:01 » en datetime (None si non parsable)."""
    m = re.match(r"\s*(\d{2})/(\d{2})\s+(\d{4})\s+(\d{2}):(\d{2})", text or "")
    if not m:
        return None
    day, month, year, hour, minute = (int(part) for part in m.groups())
    try:
        return datetime(year, month, day, hour, minute)
    except ValueError:
        return None


@dataclass
class CartridgeRecord:
    """Une entree du journal de cartouche."""

    serial: str | None = None
    type: str | None = None
    capacity: str | None = None
    first_used: str | None = None
    first_level: str | None = None
    last_used: str | None = None
    last_level: str | None = None

    @property
    def genuine(self) -> bool:
        """Un numero de serie lisible => cartouche identifiable (origine)."""
        return bool(self.serial and not re.fullmatch(r"-+", self.serial or ""))

    def as_dict(self) -> dict[str, Any]:
        """Version serialisable d'un enregistrement."""
        return {
            "serial": self.serial,
            "type": self.type,
            "capacity": self.capacity,
            "first_used": self.first_used,
            "first_level": self.first_level,
            "last_used": self.last_used,
            "last_level": self.last_level,
            "genuine": self.genuine,
        }


@dataclass
class CanonRuiData:
    """Donnees consolidees de l'IU distante."""

    reachable: bool = False
    last_update: str | None = None
    errors: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    cartridges: dict[str, list[CartridgeRecord]] = field(default_factory=dict)
    raw_error_page: str = ""
    # Table de compteurs du journal : {"C2": {"units": 4, "pages": 819}, ...}.
    # Le champ "pages" est le nombre de pages portees par le JEU DE CARTOUCHES
    # de ce type (valide empiriquement : +3 pages imprimees -> +3).
    cartridge_set_counters: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def has_error(self) -> bool:
        return bool(self.errors)

    @property
    def error_text(self) -> str:
        return " | ".join(self.errors)

    @property
    def _latest_cartridge(self) -> CartridgeRecord | None:
        """Enregistrement de cartouche le plus recent (donc le jeu en place)."""
        dated: list[tuple[datetime, CartridgeRecord]] = []
        for records in self.cartridges.values():
            for record in records:
                when = _parse_when(record.first_used)
                if when is not None:
                    dated.append((when, record))
        if not dated:
            return None
        return max(dated, key=lambda item: item[0])[1]

    @property
    def current_set_type(self) -> str | None:
        """Type du jeu de cartouches en place (ex. « C1 », « C2 »)."""
        latest = self._latest_cartridge
        return latest.type if latest else None

    @property
    def current_set_install_date(self) -> str | None:
        """Date de premiere utilisation du jeu en place."""
        latest = self._latest_cartridge
        return latest.first_used if latest else None

    @property
    def pages_with_current_set(self) -> int | None:
        """Pages imprimees depuis le montage du jeu de cartouches en place."""
        set_type = self.current_set_type
        if not set_type:
            return None
        entry = self.cartridge_set_counters.get(set_type)
        if not entry:
            return None
        return entry.get("pages")

    def as_dict(self) -> dict[str, Any]:
        """Version serialisable (le coordonnateur met les donnees en cache JSON)."""
        return {
            "reachable": self.reachable,
            "last_update": self.last_update,
            "errors": list(self.errors),
            "has_error": self.has_error,
            "counters": dict(self.counters),
            "cartridges": {
                color: [rec.as_dict() for rec in recs]
                for color, recs in self.cartridges.items()
            },
            "cartridge_set_counters": {
                key: dict(value) for key, value in self.cartridge_set_counters.items()
            },
            "cartridge_set_type": self.current_set_type,
            "cartridge_set_install_date": self.current_set_install_date,
            "cartridge_set_pages": self.pages_with_current_set,
        }


class CanonRuiClient:
    """Client de l'IU distante Canon."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        password: str,
        admin: bool = False,
        system_manager_id: str = "",
        scheme: str = "http",
    ) -> None:
        self._session = session
        self._host = host
        self._password = password
        self._admin = admin
        self._system_manager_id = system_manager_id
        self._scheme = scheme
        self._base = f"{scheme}://{host}"
        self._last_request = 0.0
        self._logged_in = False

    async def _get(self, path: str, *, expect_html: bool = True) -> str:
        # respecte l'espacement minimal entre requetes (limiteur de l'imprimante)
        delta = time.monotonic() - self._last_request
        if delta < REQUEST_SPACING:
            await asyncio.sleep(REQUEST_SPACING - delta)
        self._last_request = time.monotonic()
        timeout = aiohttp.ClientTimeout(total=CONNECT_TIMEOUT + READ_TIMEOUT)
        try:
            async with self._session.get(
                self._base + path, timeout=timeout, allow_redirects=True
            ) as resp:
                body = await resp.text(errors="replace")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CanonRuiError(f"{path}: {err}") from err
        if expect_html and resp.status >= 400:
            raise CanonRuiError(f"{path}: HTTP {resp.status}")
        return body

    async def _post(self, path: str, data: dict[str, str]) -> str:
        delta = time.monotonic() - self._last_request
        if delta < REQUEST_SPACING:
            await asyncio.sleep(REQUEST_SPACING - delta)
        self._last_request = time.monotonic()
        timeout = aiohttp.ClientTimeout(total=CONNECT_TIMEOUT + READ_TIMEOUT)
        try:
            async with self._session.post(
                self._base + path, data=data, timeout=timeout, allow_redirects=True
            ) as resp:
                return await resp.text(errors="replace")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CanonRuiError(f"POST {path}: {err}") from err

    async def async_login(self) -> None:
        """Ouvre une session. Leve CanonRuiAuthError si refusee."""
        await self._get("/")
        payload = {"i2101": self._password, "errText": "Erreur !"}
        if self._admin:
            payload["i0012"] = "1"
            if self._system_manager_id:
                payload["i0019"] = self._system_manager_id
        else:
            payload["i0017"] = "2"
            payload["i0019"] = ""
        body = await self._post("/checkLogin.cgi", payload)
        if 'name="i2101"' in body:
            raise CanonRuiAuthError("identifiants refuses par l'IU distante")
        self._logged_in = True

    async def async_fetch(self) -> CanonRuiData:
        """Recupere et parse les pages utiles."""
        if not self._logged_in:
            await self.async_login()
        data = CanonRuiData(reachable=True)
        errors_html = await self._get("/d_error.html")
        data.raw_error_page = _clean(errors_html)
        data.errors = self._parse_errors(errors_html)
        data.last_update = self._parse_last_update(errors_html)
        counters_html = await self._get("/d_counter.html")
        data.counters = self._parse_counters(counters_html)
        data.cartridges, data.cartridge_set_counters = (
            await self._async_fetch_all_cartridge_logs()
        )
        return data

    async def _async_fetch_all_cartridge_logs(
        self,
    ) -> tuple[dict[str, list[CartridgeRecord]], dict[str, dict[str, int]]]:
        """Le journal de cartouche affiche UNE couleur a la fois.

        La page contient un <select> (i2101) et un bouton 'Afficher' qui poste
        vers /cgi/cartridge_log.cgi avec un jeton iToken. On itere donc sur les
        couleurs disponibles.

        Retourne aussi la table des compteurs de jeu de cartouches (« C2: ... »),
        identique sur les quatre couleurs : elle seule donne le nombre de pages
        portees par le jeu en place, indispensable a l'estimation du niveau.
        """
        html = await self._get("/cartridge_log.html")
        token = self._parse_token(html)
        options = self._parse_log_select(html)
        out: dict[str, list[CartridgeRecord]] = {}
        set_counters = self._parse_set_counters(html)
        if not options:
            # pages multi-couleurs : on tente le parsing direct
            return self._parse_cartridge_log(html), set_counters
        for value, label in options.items():
            if not token:
                break
            page = await self._post(
                "/cgi/cartridge_log.cgi", {"iToken": token, "i2101": value, "errText": "Erreur !"}
            )
            if not set_counters:
                set_counters = self._parse_set_counters(page)
            out[self._color_key(label)] = self._parse_cartridge_rows(page)
        if not out:
            out = self._parse_cartridge_log(html)
        return out, set_counters

    @staticmethod
    def _parse_token(html: str) -> str | None:
        m = re.search(r'name="iToken"\s+value="([^"]+)"', html)
        return m.group(1) if m else None

    @staticmethod
    def _parse_set_counters(html: str) -> dict[str, dict[str, int]]:
        """Table de compteurs de jeu de cartouches du journal.

        Structure observee (derniere table de la page) :
        ``C2: | 00004 | 0000000819`` — le 2e champ est le nombre de pages
        portees par le jeu de cartouches de ce type. Valide empiriquement sur
        Canon MF660C : 3 pages imprimees -> 816 devient 819.
        """
        out: dict[str, dict[str, int]] = {}
        for cells in _rows(html):
            if len(cells) < 3:
                continue
            match = re.fullmatch(r"([A-Za-z]{1,3}\d{1,2})\s*:?", (cells[0] or "").strip())
            if not match:
                continue
            values: list[int] = []
            for raw in cells[1:3]:
                if re.fullmatch(r"\d+", (raw or "").strip()):
                    values.append(int(raw))
                else:
                    break
            if len(values) == 2:
                out[match.group(1).upper()] = {"units": values[0], "pages": values[1]}
        return out

    @staticmethod
    def _parse_log_select(html: str) -> dict[str, str]:
        """Retourne {valeur_option: libelle} du selecteur de couleur."""
        out: dict[str, str] = {}
        for m in re.finditer(
            r'<select[^>]*name="i2101"[^>]*>(.*?)</select>', html, re.S | re.I
        ):
            for opt in re.finditer(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', m.group(1), re.S | re.I):
                label = " ".join(_clean(opt.group(2)).split())
                out[opt.group(1)] = label
        return out

    @staticmethod
    def _color_key(label: str) -> str:
        low = label.lower()
        for color in ("cyan", "magenta", "jaune", "noire", "noir"):
            if color in low:
                return "noire" if color.startswith("noir") else color
        return label or "inconnu"

    @staticmethod
    def _parse_cartridge_rows(html: str) -> list[CartridgeRecord]:
        """Recupere les enregistrements du tableau d'une page de journal."""
        recs: list[CartridgeRecord] = []
        for cells in _rows(html):
            if not any(re.search(r"\d{2}/\d{2}\s+\d{4}", c or "") for c in cells):
                continue
            rec = CanonRuiClient._row_to_record(cells)
            if rec:
                recs.append(rec)
        return recs

    # ---------------- parsing ----------------

    @staticmethod
    def _parse_last_update(html: str) -> str | None:
        m = re.search(r"Derni[eè]re mise [aà] jour\s*:?\s*([0-9/: \.]{8,25})", _clean(html), re.I)
        return m.group(1).strip() if m else None

    @staticmethod
    def _parse_errors(html: str) -> list[str]:
        """Extrait les messages d'erreur (les cartouches en erreur y figurent)."""
        text = _clean(html)
        # la zone utile commence apres le titre 'Informations d'erreur'
        idx = text.find("Informations d'erreur")
        if idx < 0:
            idx = text.find("Informations d’erreur")
        body = text[idx:] if idx >= 0 else text
        # coupe le pied de page / navigation
        for stop in ("Imprimer\n", "\nImprimer", "Journal des t", "Statut t", "Fonctions du"):
            pos = body.find(stop)
            if pos > 0:
                body = body[:pos]
        body = re.sub(r"Derni[eè]re mise [aà] jour[^\n]*", " ", body, flags=re.I)
        messages: list[str] = []
        for chunk in re.split(r"\n(?=Impossible d|\d+[\)\.]|Le |La |Une |Un )", body):
            msg = " ".join(chunk.split())
            msg = msg.replace("Informations d'erreur", "").strip()
            if len(msg) > 15 and not msg.lower().startswith("l'erreur"):
                messages.append(msg)
        # deduplication en conservant l'ordre
        seen: set[str] = set()
        out = []
        for m in messages:
            if m not in seen:
                seen.add(m)
                out.append(m)
        return out

    @staticmethod
    def _parse_counters(html: str) -> dict[str, int]:
        """Retourne {libelle: valeur} depuis la page 'Verifier le compteur'."""
        counters: dict[str, int] = {}
        for cells in _rows(html):
            if len(cells) < 2:
                continue
            label = cells[0]
            value = cells[1]
            m = re.match(r"^(\d+)\s*:\s*(.+)$", label)
            if m and re.fullmatch(r"-?\d+", value or ""):
                counters[f"{m.group(1)} {m.group(2)}".strip()] = int(value)
        return counters

    @staticmethod
    def _parse_cartridge_log(html: str) -> dict[str, list[CartridgeRecord]]:
        """Parse le journal de cartouche : {couleur: [enregistrements]}.

        Structure observee : les lignes suivent l'ordre cyan, magenta, jaune,
        noire (4 premieres lignes d'en-tete de couleur), puis un tableau
        serial/type/capacite/premiere utilisation/niveau/derniere
        utilisation/niveau. Les enregistrements sont separes par des lignes
        contenant un identifiant de serie ou des tirets.
        """
        colors = ["cyan", "magenta", "jaune", "noire"]
        out: dict[str, list[CartridgeRecord]] = {c: [] for c in colors}
        rows = _rows(html)
        # on ne garde que les lignes "donnees" : au moins 5 cellules dont une date
        data_rows = [
            r
            for r in rows
            if len(r) >= 5 and any(re.search(r"\d{2}/\d{2}\s+\d{4}", " ".join(r)) for _ in [0])
        ]
        if not data_rows:
            return out
        # repartition : chaque couleur recoit len(data_rows)//4 lignes
        per = max(1, len(data_rows) // 4)
        for i, color in enumerate(colors):
            chunk = data_rows[i * per : (i + 1) * per]
            for r in chunk:
                rec = CanonRuiClient._row_to_record(r)
                if rec:
                    out[color].append(rec)
        return out

    @staticmethod
    def _row_to_record(cells: list[str]) -> CartridgeRecord | None:
        joined = " ".join(cells)
        dates = re.findall(r"(\d{2}/\d{2}\s+\d{4}\s+\d{2}:\d{2})", joined)
        levels = re.findall(r"(-?\d+\s*%|-\s*%)", joined)
        if not dates:
            return None
        serial = None
        for c in cells:
            if re.fullmatch(r"[0-9A-Za-z\-]{6,32}", c or ""):
                serial = c
                break
        rec = CartridgeRecord(
            serial=serial,
            type=next((c for c in cells if re.fullmatch(r"C\d", c or "")), None),
            capacity=next(
                (c for c in cells if c in ("Inconnu", "Démarrage") or re.fullmatch(r"\d{3,4}", c or "")),
                None,
            ),
            first_used=dates[0] if dates else None,
            first_level=levels[0].replace(" ", "") if levels else None,
            last_used=dates[1] if len(dates) > 1 else None,
            last_level=levels[1].replace(" ", "") if len(levels) > 1 else None,
        )
        return rec
