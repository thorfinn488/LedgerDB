"""LedgerDB: a durable key-value store with snapshot isolation and a B+ tree index.

Usage:
    from ledgerdb import LedgerDB, WriteConflict

    db = LedgerDB("data.wal")
    with db.transaction() as t:
        t.put("account:alice", 100)
    db.close()
"""
from .db import LedgerDB, WriteConflict, Transaction

__all__ = ["LedgerDB", "WriteConflict", "Transaction"]
