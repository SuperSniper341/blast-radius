from selector.pack import pack


def cost(workers, durations):
    return max(sum(durations[t] for t in w) for w in workers) + 2000 * len(workers)


def test_empty():
    assert pack({}) == []


def test_single_test_one_worker():
    assert pack({7: 28817}) == [[7]]


def test_never_exceeds_eight_workers():
    durations = {i: 1000 for i in range(60)}
    workers = pack(durations)
    assert 1 <= len(workers) <= 8
    flat = [t for w in workers for t in w]
    assert sorted(flat) == list(range(60))


def test_splitting_pays_only_when_halving_beats_the_worker_cost():
    two_long = {1: 10_000, 2: 10_000}
    workers = pack(two_long)
    assert len(workers) == 2
    assert cost(workers, two_long) == 10_000 + 4_000

    two_short = {1: 1_000, 2: 1_000}
    assert pack(two_short) == [[1, 2]]


def test_big_test_dominates_so_one_worker_is_cheapest():
    durations = {1: 21000, 2: 10, 3: 10}
    workers = pack(durations)
    assert workers == [[1, 2, 3]]


def test_eight_equal_tests_stay_on_one_worker():
    durations = {i: 100 for i in range(8)}
    workers = pack(durations)
    assert len(workers) == 1
    assert cost(workers, durations) == 800 + 2000
