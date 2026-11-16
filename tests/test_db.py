"""Tests for the database engine (ledgerdb/db.py).

Covers commit and rollback, snapshot isolation, deleted keys, first-committer-wins,
disjoint writes, range scans, crash recovery, torn log tails, retries, and key types.
"""
import pytest

from ledgerdb import LedgerDB, WriteConflict


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "test.wal")


def test_commit_and_rollback(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("k", 1)
    assert db.begin().get("k") == 1

    txn = db.begin()
    txn.put("k", 999)
    txn.rollback()
    assert db.begin().get("k") == 1
    db.close()


def test_read_own_writes(path):
    db = LedgerDB(path)
    txn = db.begin()
    txn.put("x", 42)
    assert txn.get("x") == 42          # sees own uncommitted write
    txn.rollback()
    assert db.begin().get("x") is None  # rolled back, so not visible
    db.close()


def test_snapshot_isolation(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("k", 1)
    reader = db.begin()                 # snapshot at ts 1
    with db.transaction() as t:
        t.put("k", 2)                   # committed at ts 2
    assert reader.get("k") == 1         # still sees ts 1 value
    reader.rollback()
    assert db.begin().get("k") == 2     # new reader sees ts 2
    db.close()


def test_deleted_key_visible_to_older_snapshot(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("k", "hello")
    reader = db.begin()                 # snapshot at ts 1
    with db.transaction() as t:
        t.delete("k")                   # committed at ts 2 as tombstone
    assert reader.get("k") == "hello"   # old snapshot still sees it
    assert db.begin().get("k") is None  # new snapshot sees deletion
    reader.rollback()
    db.close()


def test_first_committer_wins(path):
    db = LedgerDB(path)
    a = db.begin()
    b = db.begin()
    a.put("k", 10)
    b.put("k", 20)
    a.commit()
    with pytest.raises(WriteConflict):
        b.commit()
    assert db.begin().get("k") == 10
    db.close()


def test_disjoint_writes_do_not_conflict(path):
    db = LedgerDB(path)
    a = db.begin()
    b = db.begin()
    a.put("x", 1)
    b.put("y", 2)
    a.commit()
    b.commit()                           # no conflict: different keys
    r = db.begin()
    assert r.get("x") == 1
    assert r.get("y") == 2
    r.rollback()
    db.close()


def test_scan_merges_pending_writes(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("a", 1)
        t.put("c", 3)
    txn = db.begin()
    txn.put("b", 2)
    txn.delete("c")
    assert txn.scan() == [("a", 1), ("b", 2)]
    txn.rollback()
    db.close()


def test_recovery_after_reopen(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("k", "v")
    db.close()

    db = LedgerDB(path)
    assert db.begin().get("k") == "v"
    db.close()


def test_uncommitted_writes_lost_on_reopen(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("a", 1)
    txn = db.begin()
    txn.put("a", 999)
    # do NOT commit
    db.close()

    db = LedgerDB(path)
    assert db.begin().get("a") == 1     # uncommitted write is gone
    db.close()


def test_torn_tail_is_truncated_and_log_stays_usable(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("a", 1)
    db.close()

    with open(path, "ab") as f:
        f.write(b'{"t": "write", "txn": "x", "k": "b", "v"')   # crash mid-line, no newline

    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("c", 3)
    db.close()

    db = LedgerDB(path)
    assert db.begin().scan() == [("a", 1), ("c", 3)]
    db.close()


def test_run_retries_on_conflict(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("counter", 0)
    attempts = []

    def increment(txn):
        attempts.append(1)
        val = txn.get("counter")
        # simulate a conflict on first try by sneaking in a concurrent commit
        if len(attempts) == 1:
            with db.transaction() as other:
                other.put("counter", val + 100)
        txn.put("counter", val + 1)

    db.run(increment)
    r = db.begin()
    assert r.get("counter") == 101     # retried successfully on new snapshot
    r.rollback()
    assert len(attempts) == 2
    db.close()


def test_non_string_key_is_rejected(path):
    db = LedgerDB(path)
    txn = db.begin()
    with pytest.raises(TypeError):
        txn.put(123, "val")
    txn.rollback()
    db.close()


def test_history_shows_all_versions(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("k", "v1")
    with db.transaction() as t:
        t.put("k", "v2")
    with db.transaction() as t:
        t.delete("k")
    h = db.history("k")
    assert len(h) == 3
    assert h[0] == (1, "v1", False)
    assert h[1] == (2, "v2", False)
    assert h[2] == (3, None, True)
    db.close()


def test_keys_lists_all_keys(path):
    db = LedgerDB(path)
    with db.transaction() as t:
        t.put("b", 2)
        t.put("a", 1)
    assert db.keys() == ["a", "b"]      # sorted
    db.close()
