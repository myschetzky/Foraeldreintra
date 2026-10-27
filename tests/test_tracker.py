from dataclasses import dataclass
from datetime import datetime, timedelta

from custom_components.skoleintra.tracker import SeenStore


@dataclass
class Item:
    key: str
    fp: str


NOW = datetime(2026, 10, 27, 12, 0)


def test_first_run_is_silent_then_new_and_changed():
    store = SeenStore()
    assert store.diff("b:beskeder", [Item("a", "1")], NOW) == []
    changes = store.diff("b:beskeder", [Item("a", "2"), Item("b", "1")], NOW)
    assert [(c.kind, c.key) for c in changes] == [("ændret", "a"), ("ny", "b")]
    assert store.diff("b:beskeder", [Item("a", "2"), Item("b", "1")], NOW) == []


def test_temporary_empty_result_does_not_refire():
    store = SeenStore()
    store.diff("s", [Item("a", "1")], NOW)
    store.diff("s", [], NOW + timedelta(hours=1))  # fx layoutfejl/oprydning
    assert store.diff("s", [Item("a", "1")], NOW + timedelta(hours=2)) == []


def test_retention_prunes_old_keys():
    store = SeenStore(retention_days=10)
    store.diff("s", [Item("a", "1")], NOW)
    store.diff("s", [], NOW + timedelta(days=11))
    assert store.known_keys("s") == set()


def test_duplicate_keys_get_suffix():
    store = SeenStore()
    store.diff("s", [Item("dansk", "1")], NOW)
    changes = store.diff("s", [Item("dansk", "1"), Item("dansk", "2")], NOW)
    assert [(c.kind, c.key) for c in changes] == [("ny", "dansk#2")]


def test_shared_scope_seeds_per_marker_and_survives_restart():
    store = SeenStore()
    store.diff("konto:beskeder", [Item("t1", "1")], NOW, marker="konto:beskeder@a")
    # Barn B ses første gang: dets tråde registreres stille, selv om scope allerede findes.
    assert store.diff("konto:beskeder", [Item("t2", "1")], NOW, marker="konto:beskeder@b") == []
    restored = SeenStore(store.data)  # efter genstart (data fra Store)
    changes = restored.diff("konto:beskeder", [Item("t3", "1")], NOW, marker="konto:beskeder@b")
    assert [c.key for c in changes] == ["t3"]
