"""Syntetiske ForældreIntra-sider.

Opbygget ud fra de selektorer og JSON-felter, som fskintra, pyskoleintra, CagosDk/ha-foraeldreintra
og CavaleriDK/skoleintra bruger mod rigtige skoler. De er IKKE kopier af Vinderød Privatskoles sider —
kør tools/probe.py mod skolen for at bekræfte strukturen.
"""

import html
import json
from urllib.parse import quote

LOGIN_PAGE = """<html><body>
<form action="/Account/IdpLogin" method="post">
  <input name="__RequestVerificationToken" type="hidden" value="tok123">
  <input type="radio" name="RoleType" value="Parent"> <input type="radio" name="RoleType" value="Student">
  <input id="UserName" name="UserName" type="text"><input id="Password" name="Password" type="password">
  <button type="submit">Log ind</button>
</form></body></html>"""

LOGIN_PAGE_ERROR = LOGIN_PAGE.replace(
    "<form", '<div class="validation-summary-errors"><ul><li>Forkert brugernavn eller adgangskode</li></ul></div><form'
)

SAML_RELAY = """<html><body onload="document.forms[0].submit()">
<form method="post" action="/sso/acs" id="samlform">
<input type="hidden" name="SAMLResponse" value="PHNhbWw+"><input type="hidden" name="RelayState" value="abc">
<noscript><button>Fortsæt</button></noscript></form></body></html>"""

FRONTPAGE = """<html><body>
<div id="sk-personal-menu-container">
  <a href="/parent/1111/Storebror/Index">Storebror</a>
  <a href="/parent/2222/Lilles%C3%B8ster/Index">Lillesøster</a>
  <a href="/parent/1111/Storebror/settings/Index">Indstillinger</a>
</div>
<div class="sk-news-item">Velkommen</div>
</body></html>"""

DIARY_OVERVIEW = """<html><body><ul class="sk-list">
<li><a href="/parent/1111/Storebroritem/weeklyplansandhomework/diary/987">8.v lektiebog</a></li>
</ul></body></html>"""

DIARY_NOTES_TABLE = """<html><body><div id="sk-diary-notes-container"><ul class="sk-list">
<li><div class="sk-white-box"><b>Tirsdag, 27. okt. 2026:</b></div>
  <div class="sk-user-input"><table><tbody>
    <tr><th>Fag</th><th>Lektier</th></tr>
    <tr><td>DANSK</td><td>Læs side 12-20 i <a href="/file/x.pdf">noveller</a></td></tr>
    <tr><td>MATEMATIK</td><td>Opgave 4.1-4.6</td></tr>
    <tr><td>&nbsp;</td><td></td></tr>
  </tbody></table></div></li>
<li><div class="sk-white-box"><b>Onsdag, 28. okt. 2026:</b></div>
  <div class="sk-user-input"><p><strong>Engelsk:</strong> Glosetest ch. 3</p><p>Husk ordbog</p>
  <p><strong>Tysk</strong></p><p>Lær verber</p></div></li>
</ul></div></body></html>"""

DIARY_NEXT_MONTH = """<html><body><div id="sk-diary-notes-container"><ul class="sk-list">
<li><div class="sk-white-box"><b>Mandag, 2. nov. 2026:</b></div>
  <div class="sk-user-input"><table><tbody><tr><td>FYSIK</td><td>Rapport afleveres</td></tr></tbody></table></div></li>
</ul></div></body></html>"""


def _weekplan_json(week: int, year: int) -> str:
    data = {
        "SelectedPlan": {
            "ClassOrGroup": "4.s",
            "FormattedWeek": f"{week}",
            "GeneralPlan": {"LessonPlans": [
                {"Subject": {"FormattedTitle": "Dansk"}, "Content": quote("<p>Læs 20 min hver dag</p>")},
                {"Subject": {"FormattedTitle": "(uden angivelse af fag)"}, "Content": "<p>Husk idrætstøj</p>"},
            ]},
            "DailyPlans": [
                {"Date": "2026-10-26T00:00:00", "Day": "Mandag", "FormattedDate": "26. okt.",
                 "LessonPlans": [{"Subject": {"FormattedTitle": "Matematik"}, "Content": "<p>Side 30</p>"}]},
                {"Date": "", "Day": "Tirsdag", "FormattedDate": "27. okt.",
                 "LessonPlans": [{"Subject": {"FormattedTitle": "Natur/teknologi"},
                                  "Content": quote("<b>Tag</b> en blyant med")}]},
                {"Day": "Onsdag", "LessonPlans": []},
            ],
        }
    }
    return html.escape(json.dumps(data), quote=True)


def weekplan_page(week: int = 44, year: int = 2026) -> str:
    return f"""<html><body><div id="root" data-clientlogic-settings-WeeklyPlansApp="{_weekplan_json(week, year)}">
</div></body></html>"""


WEEKPLAN_LIST = """<html><body><ul class="sk-weekly-plans-list-container">
<li><a href="/parent/2222/Lilles%C3%B8steritem/weeklyplansandhomework/item/class/44-2026">Uge 44</a></li>
<li><a href="/parent/2222/Lilles%C3%B8steritem/weeklyplansandhomework/item/class/45-2026">Uge 45</a></li>
</ul></body></html>"""


def conversations_page(threads: list[dict]) -> str:
    data = {"Conversations": threads, "DataProviderSettings": {"x": "/y"}}
    return f"""<html><body><div class="sk-l-content-wrapper"><div
data-clientlogic-settings-MessageConversationsWithArchiveFunctionality="{html.escape(json.dumps(data), quote=True)}">
</div></div></body></html>"""


THREADS = [
    {"ThreadId": "aaaa-1", "LatestMessageId": 701, "Subject": "Tur til Zoo fredag", "Date": "07:16",
     "IsUnread": True, "ProfileImageViewModel": {"Name": "Mette Hansen (MH)"}, "ThreadParticipantsString": "MH"},
    {"ThreadId": "bbbb-2", "LatestMessageId": 650, "Subject": "Fødselsdag", "Date": "20. okt.",
     "IsUnread": False, "SenderName": "Forælder til Ida"},
]

THREAD_BODY = json.dumps([
    {"Id": 700, "SenderName": "Mette Hansen (MH)", "BaseText": "<p>Første besked</p>"},
    {"Id": 701, "SenderName": "Mette Hansen (MH)", "BaseText": "<p>Husk madpakke<br>og regntøj</p>"},
])

CONTACT_BOOK = """<html><body><div class="sk-contactbook">
<div class="sk-contactbook-note-container">
  <div class="sk-news-item-author"><span>Anna Forælder</span> til <span>Klasselærer</span></div>
  <div class="sk-contactbook-note-date">24. okt. 2026</div>
  <div class="sk-contactbook-note"><p>Storebror er syg i dag.</p></div>
  <div class="sk-contactbook-note-actions"><a href="/parent/1111/Storebror/contactbook/notes/replynote/19961">Svar</a></div>
  <small>Set af: Lars Lærer</small>
</div>
<div class="sk-contactbook-note-container answer">
  <div class="sk-news-item-author"><span>Lars Lærer</span></div>
  <div class="sk-contactbook-note-date">26. okt. 2026</div>
  <div class="sk-contactbook-note">God bedring!</div>
  <div class="sk-contactbook-note-actions"><a href="/parent/1111/Storebror/contactbook/notes/replynote/19962">Svar</a></div>
</div>
</div></body></html>"""

CALENDAR_JSON = json.dumps([
    {"originalId": 5, "id": "5", "title": "Efterårsferie slut", "allDay": True,
     "start": "2026-10-26T00:00:00", "end": "2026-10-26T00:00:00", "location": ["Skolen"]},
    {"id": "6", "title": "Forældremøde 8.v", "allDay": False,
     "startDate": "/Date(1793898000000)/", "endDate": "/Date(1793905200000)/", "description": "<p>Aula</p>"},
])

BLOCKED_PAGE = "<html><body><h1>Fejl</h1><h2>Der opstod en fejl</h2></body></html>"
