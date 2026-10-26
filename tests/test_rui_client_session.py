"""Test d'integration : session expiree de l'IU distante -> reconnexion + vraies donnees.

Un faux serveur aiohttp imite l'IU distante Canon :
  * une page protegee demandee sans session redirige (302) vers /t_sessError.html,
    qui repond **200** avec « La session a expiré. » (comportement reel, verifie
    sur le MF660C le 26/10/2026 : c'est pour cela qu'aucune exception n'etait
    levee et que le client servait des donnees vides indefiniment) ;
  * un login valide redonne acces aux vraies pages.

Ce que le test prouve : `async_fetch()` se reconnecte de lui-meme et rend des
donnees completes, au lieu de rendre un dict vide que le coordonnateur aurait
publie (capteurs a `unavailable` jusqu'au redemarrage de Home Assistant).

Lancer : /home/batman/venvs/printer/bin/python tests/test_rui_client_session.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

COMPOSANT = Path(__file__).resolve().parents[1] / "custom_components" / "canon_printer"
spec = importlib.util.spec_from_file_location("canon_rui", COMPOSANT / "canon_rui.py")
canon_rui = importlib.util.module_from_spec(spec)
sys.modules["canon_rui"] = canon_rui
spec.loader.exec_module(canon_rui)

CanonRuiClient = canon_rui.CanonRuiClient
CanonRuiAuthError = canon_rui.CanonRuiAuthError

MOT_DE_PASSE = "secret"
PAGE_SESSION = "<html><body><p>La session a expiré.</p></body></html>"
PAGE_LOGIN = (
    '<html><body><form action="/checkLogin.cgi" method="post">'
    '<input type="password" name="i2101"></form></body></html>'
)


class FauxIU:
    """Etat du faux serveur : une session valide a la fois."""

    def __init__(self) -> None:
        self.logins = 0
        self.jetons_valides: set[str] = set()

    def _session_ok(self, request: web.Request) -> bool:
        return request.cookies.get("SID") in self.jetons_valides

    def _redir_session(self) -> web.Response:
        return web.Response(status=302, headers={"Location": "/t_sessError.html"})

    async def accueil(self, request: web.Request) -> web.Response:
        return web.Response(text=PAGE_LOGIN, content_type="text/html")

    async def sess_error(self, request: web.Request) -> web.Response:
        return web.Response(text=PAGE_SESSION, content_type="text/html")

    async def login(self, request: web.Request) -> web.Response:
        data = await request.post()
        if data.get("i2101") != MOT_DE_PASSE:
            return web.Response(text=PAGE_LOGIN, content_type="text/html")
        self.logins += 1
        jeton = f"sid{self.logins}"
        self.jetons_valides = {jeton}
        resp = web.Response(status=302, headers={"Location": "/portal_top.html"})
        resp.set_cookie("SID", jeton)
        return resp

    async def protege(self, request: web.Request) -> web.Response:
        if not self._session_ok(request):
            return self._redir_session()
        return web.Response(text=PAGES[request.path], content_type="text/html")

    async def journal_post(self, request: web.Request) -> web.Response:
        if not self._session_ok(request):
            return self._redir_session()
        return web.Response(text=PAGES["/cgi/cartridge_log.cgi"], content_type="text/html")

    def invalider(self) -> None:
        """Fait expirer toutes les sessions, comme le fait l'imprimante en veille."""
        self.jetons_valides = set()


PAGES = {
    "/d_error.html": (
        "<html><body><p>Dernière mise à jour : 26/10/2026 14:53</p>"
        "<p>Aucune erreur.</p></body></html>"
    ),
    "/d_counter.html": (
        "<html><body><table>"
        "<tr><td>001 : Total</td><td>01377</td></tr>"
        "<tr><td>113 : Noir et blanc</td><td>01190</td></tr>"
        "<tr><td>123 : Quadri</td><td>00187</td></tr>"
        "</table></body></html>"
    ),
    "/portal_top.html": (
        "<html><body><p>Statut du périphérique</p>"
        "<p>Imprimante :</p><p>Prêt</p></body></html>"
    ),
    "/cartridge_log.html": (
        "<html><body>"
        '<input type="hidden" name="iToken" value="jeton123">'
        '<select name="i2101"><option value="1">Noire</option></select>'
        "<table><tr><td>C2:</td><td>00004</td><td>00000708</td></tr></table>"
        "</body></html>"
    ),
    "/cgi/cartridge_log.cgi": (
        "<html><body><table>"
        "<tr><td>----------</td><td>C2</td><td>Inconnu</td>"
        "<td>10/05 2026 15:01</td><td>-%</td><td>26/10 2026 12:20</td><td>-%</td></tr>"
        "</table></body></html>"
    ),
}


async def _serveur(faux: FauxIU):
    app = web.Application()
    app.router.add_get("/", faux.accueil)
    app.router.add_get("/t_sessError.html", faux.sess_error)
    app.router.add_post("/checkLogin.cgi", faux.login)
    app.router.add_post("/cgi/cartridge_log.cgi", faux.journal_post)
    for chemin in PAGES:
        if not chemin.startswith("/cgi/"):
            app.router.add_get(chemin, faux.protege)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
    return runner, port


async def _scenario() -> None:
    faux = FauxIU()
    runner, port = await _serveur(faux)
    try:
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        ) as session:
            client = CanonRuiClient(
                session,
                host=f"127.0.0.1:{port}",
                password=MOT_DE_PASSE,
            )

            # 1) lecture nominale
            donnees = await client.async_fetch()
            assert donnees.reachable is True
            assert donnees.counters, "les compteurs doivent etre lus"
            assert donnees.cartridges, "le journal de cartouches doit etre lu"
            assert donnees.cartridge_set_counters == {"C2": {"units": 4, "pages": 708}}
            assert faux.logins == 1

            # 2) la session expire cote imprimante (veille profonde)
            faux.invalider()
            donnees = await client.async_fetch()
            assert faux.logins == 2, "le client doit s'etre reconnecte tout seul"
            assert donnees.counters, "une session expiree ne doit pas rendre des donnees vides"
            assert donnees.cartridges, "le journal doit revenir apres reconnexion"

            # 3) identifiants refuses -> erreur explicite (jamais de donnees vides)
            client._password = "mauvais"  # noqa: SLF001
            faux.invalider()
            try:
                await client.async_fetch()
            except CanonRuiAuthError:
                pass
            else:
                raise AssertionError("un mot de passe refuse doit lever CanonRuiAuthError")
    finally:
        await runner.cleanup()


def test_session_expiree_reconnecte_et_rend_les_donnees():
    asyncio.run(_scenario())


if __name__ == "__main__":
    passes = echecs = 0
    for nom in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[nom]()
            print(f"  PASS {nom}")
            passes += 1
        except AssertionError as err:
            print(f"  FAIL {nom}: {err}")
            echecs += 1
    print(f"\n{passes} passes, {echecs} echecs")
    sys.exit(1 if echecs else 0)
