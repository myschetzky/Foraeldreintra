"""Konstanter for SkoleIntra (ForældreIntra)-integrationen."""

from __future__ import annotations

DOMAIN = "skoleintra"

CONF_SCHOOL_URL = "school_url"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_LOGIN_TYPE = "login_type"

LOGIN_TYPE_ALM = "alm"
LOGIN_TYPE_UNI = "uni"

# Options
OPT_CHILD_SOURCES = "child_sources"  # {barn: "lektiebog" | "ugeplan" | "begge"}
OPT_OWN_NAMES = "own_names"  # kommasepareret, bruges til kontaktbog-retning
OPT_FAST_MINUTES = "fast_minutes"  # beskeder + kontaktbog i dagtimer
OPT_SLOW_MINUTES = "slow_minutes"  # lektier + ugeplan
OPT_CALENDAR_HOURS = "calendar_hours"
OPT_QUIET_START = "quiet_start"  # time, hvor natte-tempo starter
OPT_QUIET_END = "quiet_end"  # time, hvor dag-tempo starter igen

SOURCE_DIARY = "lektiebog"
SOURCE_WEEKPLAN = "ugeplan"
SOURCE_BOTH = "begge"

DEFAULT_FAST_MINUTES = 15
DEFAULT_SLOW_MINUTES = 60
DEFAULT_CALENDAR_HOURS = 6
DEFAULT_QUIET_START = 22
DEFAULT_QUIET_END = 6
NIGHT_FACTOR = 4  # om natten polles der 4x sjældnere

# Kategorier
CAT_MESSAGES = "beskeder"
CAT_CONTACTBOOK = "kontaktbog"
CAT_DIARY = "lektiebog"
CAT_WEEKPLAN = "ugeplan"
CAT_CALENDAR = "kalender"
CATEGORIES = (CAT_MESSAGES, CAT_CONTACTBOOK, CAT_DIARY, CAT_WEEKPLAN, CAT_CALENDAR)
FAST_CATEGORIES = (CAT_MESSAGES, CAT_CONTACTBOOK)
SLOW_CATEGORIES = (CAT_DIARY, CAT_WEEKPLAN)

EVENT_NEW_ITEM = "skoleintra_new_item"

TICK_SECONDS = 300  # koordinatorens grundtakt; de fleste ticks laver ingen HTTP-kald
MAX_EVENTS_PER_CATEGORY = 10  # beskyt mod notifikationsstorm
SEEN_RETENTION_DAYS = 120
LAYOUT_ISSUE_THRESHOLD = 3  # antal fejl i træk før der oprettes et reparationsproblem

STORAGE_VERSION = 1
