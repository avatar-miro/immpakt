from immpakt.immich import Asset
from immpakt.selector import pick

POOL = [Asset(id=f"a{i:02d}") for i in range(10)]


def test_random_order_shows_every_photo_before_repeating():
    seen = [pick(POOL, c, seed=42, order="random").id for c in range(len(POOL))]
    assert sorted(seen) == sorted(a.id for a in POOL)


def test_permutation_reshuffles_on_the_next_pass():
    n = len(POOL)
    first = [pick(POOL, c, 42, "random").id for c in range(n)]
    second = [pick(POOL, c, 42, "random").id for c in range(n, 2 * n)]
    assert sorted(first) == sorted(second)
    assert first != second, "a new pass should not replay the same order"


def test_same_cursor_is_stable_within_a_pass():
    assert pick(POOL, 3, 42, "random").id == pick(POOL, 3, 42, "random").id


def test_two_devices_diverge():
    a = [pick(POOL, c, 1, "random").id for c in range(len(POOL))]
    b = [pick(POOL, c, 2, "random").id for c in range(len(POOL))]
    assert a != b


def test_sequential_orders_walk_the_pool_in_place():
    assert [pick(POOL, c, 0, "newest").id for c in range(3)] == ["a00", "a01", "a02"]


def test_cursor_wraps_past_the_end_of_the_pool():
    assert pick(POOL, len(POOL), 0, "newest").id == "a00"


def test_empty_pool_returns_none():
    assert pick([], 0, 0, "random") is None
