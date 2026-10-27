#!/usr/bin/env python3
"""Test alle ForældreIntra-datakilder mod din skole, uden Home Assistant.

    pip install aiohttp beautifulsoup4
    export SKOLEINTRA_USERNAME=... SKOLEINTRA_PASSWORD=...      # eller --secrets sti/til/secrets.yaml
    python3 tools/probe.py https://vinderoedprivatskole.m.skoleintra.dk

Rapporten indeholder kun antal, fag og datoer — ikke beskedtekster eller navne på afsendere
(brug --verbose for at se dem lokalt). --dump MAPPE gemmer de rå sider til fejlfinding;
de indeholder persondata, så del dem ikke offentligt.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib
import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

import aiohttp

# Indlæs integrationens rene moduler uden at importere Home Assistant.
PKG_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "skoleintra"
pkg = types.ModuleType("skoleintra_core")
pkg.__path__ = [str(PKG_DIR)]
sys.modules["skoleintra_core"] = pkg
api = importlib.import_module("skoleintra_core.api")
errors = importlib.import_module("skoleintra_core.errors")


def read_secrets(path: str) -> dict[str, str]:
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class DumpingClient(api.SkoleIntraClient):
    dump_dir: Path | None = None

    async def get(self, path: str) -> str:
        text = await super().get(path)
        if self.dump_dir:
            name = path.strip("/").replace("/", "_").split("?")[0][:120] or "root"
            (self.dump_dir / f"{name}.html").write_text(text, encoding="utf-8")
        return text


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("school_url")
    ap.add_argument("--login-type", default="alm", choices=["alm", "uni"])
    ap.add_argument("--secrets", help="secrets.yaml med skoleintra_username/skoleintra_password")
    ap.add_argument("--verbose", action="store_true", help="vis indhold (kun lokalt!)")
    ap.add_argument("--dump", help="gem rå sider i denne mappe")
    args = ap.parse_args()

    sec = read_secrets(args.secrets) if args.secrets else {}
    user = sec.get("skoleintra_username") or os.environ.get("SKOLEINTRA_USERNAME") or input("Brugernavn: ")
    pwd = sec.get("skoleintra_password") or os.environ.get("SKOLEINTRA_PASSWORD") or getpass.getpass("Adgangskode: ")
    if args.dump:
        DumpingClient.dump_dir = Path(args.dump)
        DumpingClient.dump_dir.mkdir(parents=True, exist_ok=True)

    ok = True
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as session:
        client = DumpingClient(session, args.school_url, user, pwd, args.login_type)
        print(f"Skole: {client.base_url}")
        try:
            children = await client.login()
        except errors.SkoleIntraError as err:
            print(f"LOGIN FEJLEDE [{type(err).__name__}]: {err}")
            return 2
        print(f"Login OK. Børn: {', '.join(f'{c.name} ({c.path})' for c in children)}\n")

        now = datetime.now(api.parsers.TZ)
        for child in children:
            print(f"=== {child.name} ===")
            checks = {
                "lektiebog": lambda c=child: client.get_diary(c),
                "ugeplan": lambda c=child: client.get_weekplans(c),
                "beskeder": lambda c=child: client.get_messages(c),
                "kontaktbog": lambda c=child: client.get_contact_book(c),
                "kalender": lambda c=child: client.get_calendar(c, now - timedelta(days=7), now + timedelta(days=60)),
            }
            for name, fn in checks.items():
                try:
                    result = await fn()
                except errors.SkoleIntraError as err:
                    ok = False
                    print(f"  {name:11} FEJL [{type(err).__name__}] {err}")
                    continue
                print(f"  {name:11} OK  {summarize(name, result, args.verbose)}")
            print()
        print(f"I alt {client.request_count} HTTP-kald, {client.login_count} login.")
    return 0 if ok else 1


def summarize(name: str, result, verbose: bool) -> str:
    if name == "lektiebog":
        days = sorted({i.day.isoformat() for i in result if i.day})
        s = f"{len(result)} lektier, datoer {days[:6]}, fag {sorted({i.subject for i in result})[:8]}"
        if verbose:
            s += "".join(f"\n      {i.day} {i.subject}: {i.text[:80]!r}" for i in result[:10])
        return s
    if name == "ugeplan":
        parts = [f"uge {p.week_id}: {len(p.general)} generelle + {len(p.days)} dagspunkter" for p in result]
        s = "; ".join(parts) or "ingen ugeplan for denne/næste uge"
        if verbose:
            s += "".join(f"\n      {i.day_label} {i.subject}: {i.text[:80]!r}" for p in result for i in p.days[:10])
        return s
    if name == "beskeder":
        s = f"{len(result)} tråde, {sum(t.unread for t in result)} ulæste, {sum(bool(t.body) for t in result)} med brødtekst"
        if verbose:
            s += "".join(f"\n      {t.date_text} {t.sender}: {t.subject!r}" for t in result[:10])
        return s
    if name == "kontaktbog":
        s = f"{len(result)} noter, seneste dato {result[0].date_text if result else '-'}"
        if verbose:
            s += "".join(f"\n      {n.date_text} {n.author}: {n.text[:80]!r}" for n in result[:10])
        return s
    if name == "kalender":
        s = f"{len(result)} begivenheder"
        if verbose:
            s += "".join(f"\n      {e.start} {e.title!r}" for e in result[:10])
        return s
    return str(result)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
