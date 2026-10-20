"""
Квесты дня без одинаковых по смыслу (п.7 владельца, 20.10): «Закрой все привычки на сегодня» и «Ни одного пропуска сегодня»
стояли рядом, хотя засчитываются одним и тем же. Теперь из группы одинакового смысла в день берётся не больше одного квеста,
а набор там, где пересечений не было, остаётся прежним (чтобы не менять квесты людям посреди дня без нужды).
"""
import hashlib
import itertools
import random

import pytest

from db import add_user
from db import quests as quests_module
from db.quests import OVERLAPPING_QUESTS, QUEST_KEYS, QUESTS_PER_DAY, _pick_quests_for_day, quests_overlap

DAYS = [f"2026-10-{d:02d}" for d in range(1, 31)]
USERS = range(1, 301)


def _plain_pick(user_id, day):
    """Как выбирали раньше: первые три из перемешанного списка."""
    seed = int(hashlib.md5(f"{user_id}:{day}".encode("utf-8")).hexdigest(), 16)
    keys = QUEST_KEYS[:]
    random.Random(seed).shuffle(keys)
    return keys[:QUESTS_PER_DAY]


def test_the_overlap_groups_name_the_pairs_the_owner_saw():
    assert quests_overlap("all_done", "no_skip") and quests_overlap("no_skip", "all_done")
    assert quests_overlap("two_habits", "two_categories")
    assert not quests_overlap("all_done", "before_noon")
    for group in OVERLAPPING_QUESTS:
        assert all(key in QUEST_KEYS for key in group), group


def test_a_day_never_has_two_quests_with_the_same_meaning():
    for user_id in USERS:
        for day in DAYS:
            picked = _pick_quests_for_day(user_id, day)
            assert len(picked) == QUESTS_PER_DAY and len(set(picked)) == QUESTS_PER_DAY, (user_id, day, picked)
            for first, second in itertools.combinations(picked, 2):
                assert not quests_overlap(first, second), (user_id, day, picked)


def test_the_pick_is_stable_and_stays_the_same_when_nothing_overlapped():
    changed = 0
    for user_id in USERS:
        for day in DAYS:
            picked = _pick_quests_for_day(user_id, day)
            assert picked == _pick_quests_for_day(user_id, day)
            plain = _plain_pick(user_id, day)
            overlapped = any(quests_overlap(a, b) for a, b in itertools.combinations(plain, 2))
            if overlapped:
                changed += 1
                assert picked != plain
                assert picked[0] == plain[0], (picked, plain)      # первый квест не страдает, пропускается только повтор
            else:
                assert picked == plain, (user_id, day, picked, plain)
    assert changed > 0, "в выборке должны были встретиться и дни с повторами, иначе тест ничего не проверяет"


def test_every_quest_still_comes_up_and_the_sets_stay_varied():
    seen, sets = set(), set()
    for user_id in USERS:
        for day in DAYS[:10]:
            picked = _pick_quests_for_day(user_id, day)
            seen.update(picked)
            sets.add(frozenset(picked))
    assert seen == set(QUEST_KEYS)
    assert len(sets) >= 8


@pytest.fixture
def uid():
    return 858_000_001


def test_the_real_list_for_a_person_has_three_distinct_quests(uid):
    add_user(uid, "q", "Q")
    for _ in range(3):
        keys = [q["key"] for q in quests_module.get_daily_quests(uid)]
        assert len(keys) == 3 and len(set(keys)) == 3
        assert not (("all_done" in keys) and ("no_skip" in keys))
        assert not (("two_habits" in keys) and ("two_categories" in keys))
