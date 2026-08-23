"""The manifest accumulates one entry per build; identity is camera + instant."""
from datetime import datetime, timedelta, timezone

from sortedcontainers import SortedSet

from app.services.blink.manifest import prune_manifest


class FakeItem:
    """Mirrors blinkpy's LocalStorageMediaItem: hash on id, eq on created_at."""

    def __init__(self, item_id, name, created_at, manifest_id):
        self._id = item_id
        self._name = name
        self._created_at = created_at
        self._manifest_id = manifest_id

    @property
    def id(self): return self._id

    @property
    def name(self): return self._name

    @property
    def created_at(self): return self._created_at

    def __eq__(self, other): return self._created_at == other._created_at
    def __lt__(self, other): return self._created_at < other._created_at
    def __hash__(self): return self._id


class FakeMod:
    def __init__(self, items, manifest_id):
        self._local_storage = {
            "manifest": SortedSet(items),
            "last_manifest_id": manifest_id,
        }


T0 = datetime(2026, 8, 22, 1, 6, tzinfo=timezone.utc)


def test_hash_and_eq_disagree_so_the_set_stores_the_clip_twice():
    """Guards the premise: this is why duplicates reach the API at all."""
    s = SortedSet([FakeItem(1, "Retro", T0, "m1")])
    s.add(FakeItem(2, "Retro", T0, "m2"))  # same clip, id re-issued
    assert len(s) == 2


def test_repeated_builds_of_one_clip_collapse():
    mod = FakeMod([FakeItem(1, "Retro", T0, "m1"), FakeItem(2, "Retro", T0, "m2")], "m2")
    clips = prune_manifest(mod)
    assert [c.id for c in clips] == [2]          # newest build wins
    assert len(mod._local_storage["manifest"]) == 1  # pruned in place


def test_current_build_wins_regardless_of_iteration_order():
    mod = FakeMod([FakeItem(9, "Retro", T0, "m2"), FakeItem(3, "Retro", T0, "m1")], "m2")
    assert [c.id for c in prune_manifest(mod)] == [9]


def test_distinct_recordings_in_the_same_minute_both_survive():
    """A motion burst is several real clips seconds apart — not duplicates."""
    mod = FakeMod([
        FakeItem(1, "Retro", T0, "m1"),
        FakeItem(2, "Retro", T0 + timedelta(seconds=35), "m1"),
    ], "m1")
    assert len(prune_manifest(mod)) == 2


def test_two_cameras_at_the_same_instant_both_survive():
    """Upstream __eq__ ignores the camera; identity here must not."""
    mod = FakeMod([
        FakeItem(1, "Retro", T0, "m1"),
        FakeItem(2, "Entrata", T0, "m1"),
    ], "m1")
    assert sorted(c.name for c in prune_manifest(mod)) == ["Entrata", "Retro"]


def test_clips_come_back_newest_first():
    mod = FakeMod([
        FakeItem(1, "Retro", T0, "m1"),
        FakeItem(2, "Retro", T0 + timedelta(minutes=5), "m1"),
    ], "m1")
    assert [c.id for c in prune_manifest(mod)] == [2, 1]


def test_untouched_when_there_is_nothing_to_collapse():
    mod = FakeMod([FakeItem(1, "Retro", T0, "m1")], "m1")
    assert len(prune_manifest(mod)) == 1
    assert len(mod._local_storage["manifest"]) == 1


def test_empty_and_missing_storage_are_survivable():
    assert prune_manifest(FakeMod([], "m1")) == []
    assert prune_manifest(object()) == []
