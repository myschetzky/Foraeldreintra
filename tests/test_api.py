"""Klienten mod en lokal falsk ForældreIntra-server (aiohttp)."""

import asyncio
from datetime import date, datetime, timedelta

import aiohttp
import pytest
from aiohttp import web

from custom_components.skoleintra import api as api_mod
from custom_components.skoleintra.api import SkoleIntraClient, normalize_school_url
from custom_components.skoleintra.errors import (
    AuthError,
    BlockedError,
    ManualActionRequired,
    TransientError,
)
from tests import fixtures as fx


class FakeSchool:
    def __init__(self) -> None:
        self.password = "hemmelig"
        self.sessions: set[str] = set()
        self.logins = 0
        self.mode = "normal"  # normal | mitid | confirm | blocked | slow
        self.posted: list[dict] = []

    def app(self) -> web.Application:
        app = web.Application()
        r = app.router
        r.add_get("/Account/IdpLogin", self.login_page)
        r.add_post("/Account/IdpLogin", self.login_post)
        r.add_post("/sso/acs", self.acs)
        r.add_get("/ConfirmContacts", self.confirm_page)
        r.add_get("/parent/{cid}/{name}/Index", self.guard(lambda req: fx.FRONTPAGE))
        r.add_get("/parent/1111/Storebroritem/weeklyplansandhomework/diary", self.guard(lambda req: fx.DIARY_OVERVIEW))
        r.add_get("/parent/1111/Storebroritem/weeklyplansandhomework/diary/notes/987",
                  self.guard(lambda req: fx.DIARY_NOTES_TABLE))
        r.add_get("/parent/1111/Storebroritem/weeklyplansandhomework/diary/notes/987/NextMonth",
                  self.guard(lambda req: fx.DIARY_NEXT_MONTH))
        r.add_get("/parent/2222/Lillesøsteritem/weeklyplansandhomework/list", self.guard(lambda req: fx.WEEKPLAN_LIST))
        r.add_get("/parent/2222/{name:[^/]+}item/weeklyplansandhomework/item/class/{wid}",
                  self.guard(lambda req: fx.weekplan_page(*map(int, req.match_info["wid"].split("-")))))
        r.add_get("/parent/{cid}/{name}/messages/conversations",
                  self.guard(lambda req: fx.conversations_page(fx.THREADS)))
        r.add_get("/parent/{cid}/{name}/messages/conversations/loadmessagesforselectedconversation",
                  self.guard(lambda req: fx.THREAD_BODY))
        r.add_get("/parent/{cid}/{name}/contactbook/notes", self.guard(lambda req: fx.CONTACT_BOOK))
        r.add_get("/parent/{cid}/{name}/calendareventsource/SchoolEvents", self.guard(lambda req: fx.CALENDAR_JSON))
        return app

    def guard(self, render):
        async def handler(request: web.Request):
            if self.mode == "blocked":
                return web.Response(status=500, text=fx.BLOCKED_PAGE)
            if self.mode == "slow":
                await asyncio.sleep(2)
            if request.cookies.get("sid") not in self.sessions:
                raise web.HTTPFound("/Account/IdpLogin?ReturnUrl=" + request.path)
            return web.Response(text=render(request), content_type="text/html")
        return handler

    async def confirm_page(self, request):
        return web.Response(text="<html><form action='/Confirm'></form></html>", content_type="text/html")

    async def login_page(self, request):
        if self.mode == "mitid":
            return web.Response(text="<html><a href='/mitid'>Log ind med MitID</a></html>", content_type="text/html")
        return web.Response(text=fx.LOGIN_PAGE, content_type="text/html")

    async def login_post(self, request):
        data = dict(await request.post())
        self.posted.append(data)
        if data.get("Password") != self.password or data.get("RoleType") != "Parent" \
                or data.get("__RequestVerificationToken") != "tok123":
            return web.Response(text=fx.LOGIN_PAGE_ERROR, content_type="text/html")
        return web.Response(text=fx.SAML_RELAY, content_type="text/html")

    async def acs(self, request):
        data = await request.post()
        assert data["SAMLResponse"] == "PHNhbWw+"
        if self.mode == "confirm":
            raise web.HTTPFound("/ConfirmContacts")
        self.logins += 1
        sid = f"s{self.logins}"
        self.sessions.add(sid)
        resp = web.HTTPFound("/parent/1111/Storebror/Index")
        resp.set_cookie("sid", sid)
        raise resp


@pytest.fixture
async def school(socket_enabled, aiohttp_server, monkeypatch):
    monkeypatch.setattr(SkoleIntraClient, "STRICT", False)
    monkeypatch.setattr(api_mod, "MIN_REQUEST_GAP", 0)
    fake = FakeSchool()
    server = await aiohttp_server(fake.app())
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        fake.client = SkoleIntraClient(session, str(server.make_url("")), "forælder", fake.password)
        yield fake


def test_normalize_school_url():
    assert normalize_school_url("vinderoedprivatskole") == "https://vinderoedprivatskole.m.skoleintra.dk"
    assert normalize_school_url("https://vinderoedprivatskole.m.skoleintra.dk/parent/1/A/Index") == \
        "https://vinderoedprivatskole.m.skoleintra.dk"


async def test_login_finds_both_children(school):
    kids = await school.client.login()
    assert [k.name for k in kids] == ["Storebror", "Lillesøster"]
    assert school.posted[0]["UserName"] == "forælder"


async def test_all_sources(school):
    await school.client.login()
    storebror, lillesoster = school.client.children
    diary = await school.client.get_diary(storebror)
    assert {i.subject for i in diary} == {"Dansk", "Matematik", "Engelsk", "Tysk", "Fysik"}
    plans = await school.client.get_weekplans(lillesoster, today=date(2026, 10, 27))
    assert [p.week_id for p in plans] == ["44-2026", "45-2026"]
    msgs = await school.client.get_messages(storebror, known={"bbbb-2:650"})
    assert msgs[0].body == "Husk madpakke\nog regntøj" and msgs[1].body == ""  # kendt tråd hentes ikke
    notes = await school.client.get_contact_book(storebror)
    assert len(notes) == 2
    now = datetime(2026, 10, 27, tzinfo=api_mod.parsers.TZ)
    cal = await school.client.get_calendar(storebror, now, now + timedelta(days=30))
    assert len(cal) == 2


async def test_expired_session_relogs_transparently(school):
    await school.client.login()
    school.sessions.clear()  # skolen smider sessionen ud
    notes = await school.client.get_contact_book(school.client.children[0])
    assert len(notes) == 2
    assert school.logins == 2


async def test_wrong_password(school):
    school.password = "ny-kode"
    with pytest.raises(AuthError, match="Forkert brugernavn"):
        await school.client.login()
    assert len(school.posted) == 1  # prøver ikke igen og igen (ingen lockout)


async def test_mitid_and_confirm_contacts_need_manual_action(school):
    school.mode = "mitid"
    with pytest.raises(ManualActionRequired):
        await school.client.login()
    school.mode = "confirm"
    with pytest.raises(ManualActionRequired, match="kontaktoplysninger"):
        await school.client.login()


async def test_blocked_and_timeout(school, monkeypatch):
    await school.client.login()
    school.mode = "blocked"
    with pytest.raises(BlockedError):
        await school.client.get_contact_book(school.client.children[0])
    school.mode = "slow"
    monkeypatch.setattr(api_mod, "REQUEST_TIMEOUT", aiohttp.ClientTimeout(total=0.2))
    with pytest.raises(TransientError, match="Timeout"):
        await school.client.get_contact_book(school.client.children[0])


async def test_probe_tool_end_to_end(socket_enabled, aiohttp_server, monkeypatch, capsys):
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("probe", Path(__file__).parent.parent / "tools" / "probe.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    monkeypatch.setattr(probe.api.SkoleIntraClient, "STRICT", False)
    monkeypatch.setattr(probe.api, "MIN_REQUEST_GAP", 0)
    fake = FakeSchool()
    server = await aiohttp_server(fake.app())
    real_jar = aiohttp.CookieJar
    monkeypatch.setattr(probe.aiohttp, "CookieJar", lambda **kw: real_jar(unsafe=True))  # 127.0.0.1 er en IP
    monkeypatch.setenv("SKOLEINTRA_USERNAME", "forælder")
    monkeypatch.setenv("SKOLEINTRA_PASSWORD", fake.password)
    monkeypatch.setattr(sys, "argv", ["probe.py", str(server.make_url(""))])
    assert await probe.main() == 1  # den falske skole har kun lektiebog for Storebror og ugeplan for Lillesøster
    out = capsys.readouterr().out
    assert "ugeplan     FEJL [LayoutError] HTTP 404 på /parent/1111/Storebroritem" in out
    assert "Login OK. Børn: Storebror" in out
    assert "lektiebog   OK  5 lektier" in out
    assert "kontaktbog  OK  2 noter" in out
    assert "Husk madpakke" not in out  # intet beskedindhold uden --verbose
