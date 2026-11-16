"""LedgerDB: a durable key-value store with snapshot isolation and a B+ tree index.

Every write creates a new version of a key, tagged with the commit timestamp of
the transaction that wrote it. A transaction reads the newest version committed
at or before its start timestamp, so it sees a consistent snapshot.

Commit uses first-committer-wins: if another transaction committed a change to
a key this transaction also wrote, after this one began, the commit fails with
WriteConflict.
"""
from __future__ import annotations

import contextlib
import os
import threading
import uuid

from .btree import BPlusTree
from .wal import WriteAheadLog, read_committed


class WriteConflict(Exception):
    """Another transaction committed a change to a key this one wrote, after it began."""


class _Missing:
    def __repr__(self) -> str:
        return "MISSING"


_MISSING = _Missing()


def _visible(versions: list[tuple[int, object, bool]], snapshot_ts: int):
    """Return the value of the newest version at or before snapshot_ts, or _MISSING."""
    for commit_ts, value, deleted in reversed(versions):
        if commit_ts <= snapshot_ts:
            return _MISSING if deleted else value
    return _MISSING


def _check_key(key) -> None:
    if not isinstance(key, str):
        raise TypeError("keys must be str")


class Transaction:
    def __init__(self, db: "LedgerDB", txn_id: str, start_ts: int) -> None:
        self._db = db
        self.id = txn_id
        self.start_ts = start_ts
        self.active = True
        self._writes: dict[str, tuple[object, bool]] = {}   # key -> (value, deleted)

    def _require_active(self) -> None:
        if not self.active:
            raise RuntimeError("transaction is already finished")

    def get(self, key, default=None):
        self._require_active()
        if key in self._writes:
            value, deleted = self._writes[key]
            return default if deleted else value
        value = self._db._read(key, self.start_ts)
        return default if value is _MISSING else value

    def put(self, key, value) -> None:
        self._require_active()
        _check_key(key)
        self._writes[key] = (value, False)

    def delete(self, key) -> None:
        self._require_active()
        _check_key(key)
        self._writes[key] = (None, True)

    def scan(self, lo=None, hi=None) -> list[tuple[str, object]]:
        """Sorted (key, value) pairs with lo <= key < hi, as this transaction sees them."""
        self._require_active()
        rows = dict(self._db._scan(lo, hi, self.start_ts))
        for key, (value, deleted) in self._writes.items():
            if (lo is not None and key < lo) or (hi is not None and key >= hi):
                continue
            if deleted:
                rows.pop(key, None)
            else:
                rows[key] = value
        return sorted(rows.items())

    def commit(self) -> None:
        self._require_active()
        try:
            self._db._commit(self)
        finally:
            self.active = False
            self._writes.clear()

    def pending(self) -> list[tuple[str, object, bool]]:
        """This transaction's uncommitted writes as (key, value, deleted)."""
        return [(k, v, d) for k, (v, d) in self._writes.items()]

    def rollback(self) -> None:
        if self.active:
            self._writes.clear()
            self.active = False


class LedgerDB:
    def __init__(self, wal_path: str, order: int = 8) -> None:
        self._lock = threading.Lock()
        self._index = BPlusTree(order=order)     # key -> [(commit_ts, value, deleted), ...] oldest first
        self._commit_ts = 0

        committed, valid_end = read_committed(wal_path)
        if os.path.exists(wal_path) and os.path.getsize(wal_path) > valid_end:
            os.truncate(wal_path, valid_end)     # drop a torn tail so new records start on a clean line
        for commit_ts, writes in committed:
            for record in writes:
                self._install(record["k"], record["v"], record["d"], commit_ts)
            self._commit_ts = commit_ts

        self._wal = WriteAheadLog(wal_path)

    def close(self) -> None:
        self._wal.close()

    @property
    def last_commit_ts(self) -> int:
        with self._lock:
            return self._commit_ts

    def history(self, key: str) -> list[tuple[int, object, bool]]:
        """Committed versions of key, oldest first, as (commit_ts, value, deleted)."""
        with self._lock:
            return list(self._index.get(key, []))

    def tree_dump(self) -> str:
        """Text picture of the B+ tree index."""
        with self._lock:
            return self._index.dump()

    def tree_structure(self) -> dict:
        with self._lock:
            return self._index.structure()

    def keys(self) -> list[str]:
        """Every key that has ever been written, in key order (including deleted ones)."""
        with self._lock:
            return [k for k, _ in self._index.scan()]

    def begin(self) -> Transaction:
        with self._lock:
            return Transaction(self, uuid.uuid4().hex, self._commit_ts)

    @contextlib.contextmanager
    def transaction(self):
        """Commit on normal exit, roll back if the block raises."""
        txn = self.begin()
        try:
            yield txn
        except BaseException:
            txn.rollback()
            raise
        else:
            txn.commit()

    def run(self, fn, retries: int = 5):
        """Call fn(txn) inside a transaction and commit it, retrying on WriteConflict."""
        for attempt in range(1, retries + 1):
            txn = self.begin()
            try:
                result = fn(txn)
                txn.commit()
                return result
            except WriteConflict:
                if attempt == retries:
                    raise
            except BaseException:
                txn.rollback()
                raise

    # ----- internals (the lock protects the index and commit timestamp) -----

    def _install(self, key: str, value, deleted: bool, commit_ts: int) -> None:
        version = (commit_ts, value, deleted)
        versions = self._index.get(key)
        if versions is None:
            self._index.put(key, [version])
        else:
            versions.append(version)

    def _read(self, key, snapshot_ts: int):
        with self._lock:
            versions = self._index.get(key)
            return _MISSING if versions is None else _visible(versions, snapshot_ts)

    def _scan(self, lo, hi, snapshot_ts: int) -> list[tuple[str, object]]:
        with self._lock:
            rows = []
            for key, versions in self._index.scan(lo, hi):
                value = _visible(versions, snapshot_ts)
                if value is not _MISSING:
                    rows.append((key, value))
            return rows

    def _commit(self, txn: Transaction) -> None:
        if not txn._writes:
            return                                   # read-only: nothing to log
        with self._lock:
            for key in txn._writes:
                versions = self._index.get(key)
                if versions and versions[-1][0] > txn.start_ts:
                    raise WriteConflict(
                        f"key {key!r} was changed by a transaction that committed after this one began"
                    )
            commit_ts = self._commit_ts + 1
            records = [{"t": "write", "txn": txn.id, "k": k, "v": v, "d": d}
                       for k, (v, d) in txn._writes.items()]
            self._wal.append_transaction(txn.id, commit_ts, records)   # durable before it becomes visible
            for k, (v, d) in txn._writes.items():
                self._install(k, v, d, commit_ts)
            self._commit_ts = commit_ts
