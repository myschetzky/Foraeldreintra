from datetime import date, datetime

import pytest

from custom_components.skoleintra import parsers
from custom_components.skoleintra.errors import LayoutError
from tests import fixtures as fx


def test_children_from_menu_and_url():
    kids = parsers.parse_children(fx.FRONTPAGE, "https://x.m.skoleintra.dk/parent/1111/Storebror/Index")
    assert [(c.child_id, c.name, c.path) for c in kids] == [
        ("1111", "Storebror", "/parent/1111/Storebror"),
        ("2222", "Lillesøster", "/parent/2222/Lilles%C3%B8ster"),
    ]


def test_login_and_sso_form_detection():
    assert parsers.find_login_form(fx.LOGIN_PAGE) is not None
    assert parsers.find_sso_form(fx.LOGIN_PAGE) is None
    assert parsers.find_sso_form(fx.SAML_RELAY) is not None
    assert parsers.find_login_form(fx.FRONTPAGE) is None
    assert parsers.login_error_text(fx.LOGIN_PAGE_ERROR) == "Forkert brugernavn eller adgangskode"
    assert parsers.looks_blocked(500, fx.BLOCKED_PAGE)
    assert not parsers.looks_blocked(200, fx.BLOCKED_PAGE)


def test_diary_url_variants():
    assert parsers.extract_diary_url(fx.DIARY_OVERVIEW, "/parent/1111/Storebror") == \
        "/parent/1111/Storebroritem/weeklyplansandhomework/diary/notes/987"
    assert parsers.extract_diary_url('<a href="/parent/1/A/diaries/list/notes/55">', "/parent/1/A") == \
        "/parent/1/A/diaries/list/notes/55"


def test_diary_table_and_freetext():
    items = parsers.parse_diary(fx.DIARY_NOTES_TABLE, today=date(2026, 10, 27))
    got = [(i.day, i.subject, i.text) for i in items]
    assert (date(2026, 10, 27), "Dansk", "Læs side 12-20 i noveller") in got
    assert (date(2026, 10, 27), "Matematik", "Opgave 4.1-4.6") in got
    assert (date(2026, 10, 28), "Engelsk", "Glosetest ch. 3\nHusk ordbog") in got
    assert (date(2026, 10, 28), "Tysk", "Lær verber") in got
    assert len(items) == 4  # tom række ignoreres
    dansk = next(i for i in items if i.subject == "Dansk")
    assert dansk.links == [{"tekst": "noveller", "url": "/file/x.pdf"}]


def test_diary_layout_change_is_detected():
    with pytest.raises(LayoutError):
        parsers.parse_diary("<html><body><p>Ny fancy side</p></body></html>")


def test_weekplan_json_with_urlencoded_content():
    plan = parsers.parse_weekplan(fx.weekplan_page(), "44-2026", "u")
    assert plan.title == "Ugeplan 4.s uge 44"
    assert [(i.subject, i.text) for i in plan.general] == [("Dansk", "Læs 20 min hver dag"),
                                                          ("Generelt", "Husk idrætstøj")]
    assert [(i.day, i.subject, i.text) for i in plan.days] == [
        (date(2026, 10, 26), "Matematik", "Side 30"),
        (date(2026, 10, 27), "Natur/teknologi", "Tag en blyant med"),
    ]


def test_weekplan_list_and_layout_error():
    assert [p["week_id"] for p in parsers.parse_weekplan_list(fx.WEEKPLAN_LIST)] == ["44-2026", "45-2026"]
    with pytest.raises(LayoutError):
        parsers.parse_weekplan("<html></html>", "44-2026", "u")


def test_conversations_and_body():
    threads = parsers.parse_conversations(fx.conversations_page(fx.THREADS))
    assert [(t.key, t.subject, t.sender, t.unread) for t in threads] == [
        ("aaaa-1:701", "Tur til Zoo fredag", "Mette Hansen (MH)", True),
        ("bbbb-2:650", "Fødselsdag", "Forælder til Ida", False),
    ]
    assert parsers.parse_thread_body(fx.THREAD_BODY, "701") == ("Mette Hansen (MH)", "Husk madpakke\nog regntøj")
    assert parsers.parse_conversations(fx.conversations_page([])) == []
    with pytest.raises(LayoutError):
        parsers.parse_conversations("<html><body>helt andet</body></html>")


def test_contact_book_direction_and_order():
    notes = parsers.parse_contact_book(fx.CONTACT_BOOK, own_names=["Anna Forælder"])
    assert [(n.note_id, n.author, n.from_us, n.is_reply) for n in notes] == [
        ("19962", "Lars Lærer", False, True),
        ("19961", "Anna Forælder", True, False),
    ]
    assert notes[1].seen_by == "Lars Lærer"
    assert parsers.parse_contact_book(fx.CONTACT_BOOK)[0].from_us is None


def test_calendar_dotnet_and_all_day():
    events = parsers.parse_calendar(fx.CALENDAR_JSON)
    allday, meeting = events
    assert allday.all_day and allday.start == date(2026, 10, 26) and allday.end == date(2026, 10, 27)
    assert allday.location == "Skolen"
    assert isinstance(meeting.start, datetime) and meeting.start.tzinfo is not None
    assert meeting.description == "Aula"
    with pytest.raises(LayoutError):
        parsers.parse_calendar("<html>login</html>")


@pytest.mark.parametrize("text,expected", [
    ("Tirsdag, 27. okt. 2026:", date(2026, 10, 27)),
    ("Mandag 26 maj 2025", date(2025, 5, 26)),
    ("27.10.2026", date(2026, 10, 27)),
    ("2026-10-27T00:00:00", date(2026, 10, 27)),
    ("27. okt.", date(2026, 10, 27)),
    ("ingen dato", None),
])
def test_danish_dates(text, expected):
    assert parsers.parse_danish_date(text, default_year=2026) == expected
