"""Write skew demonstration for the LedgerDB ADBMS PBL report.

Two scenarios:
  1. naive   – both transactions commit, nobody is on call (rule broken)
  2. fixed   – a shared lock key forces a conflict, rule preserved

Run:   python examples/write_skew_demo.py
"""
import os
import sys
import tempfile

# Allow running directly from the examples/ folder.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ledgerdb import LedgerDB, WriteConflict


def on_call(txn):
    """Return the list of doctors currently on call as seen by txn."""
    return [k for k, v in txn.scan("doctor:", "doctor;") if v == "on"]


def setup(db):
    """Seed the database: both doctors are on call."""
    with db.transaction() as t:
        t.put("doctor:alice", "on")
        t.put("doctor:bob", "on")


def naive(db):
    """Naive version: both transactions write different keys, no conflict."""
    a, b = db.begin(), db.begin()
    if len(on_call(a)) >= 2:
        a.put("doctor:alice", "off")
    if len(on_call(b)) >= 2:
        b.put("doctor:bob", "off")
    a.commit()
    b.commit()
    print("naive: both committed")
    result = on_call(db.begin()) or "nobody"
    print("  on call now:", result)


def with_conflict(db):
    """Fixed version: a shared lock key forces a conflict."""
    a, b = db.begin(), db.begin()
    for txn, doctor in ((a, "doctor:alice"), (b, "doctor:bob")):
        if len(on_call(txn)) >= 2:
            txn.put("lock:oncall", "held")   # both transactions now write the same key
            txn.put(doctor, "off")
    a.commit()
    try:
        b.commit()
        print("with lock key: both committed")
    except WriteConflict as err:
        print("with lock key: second commit rejected ->", err)
    result = on_call(db.begin()) or "nobody"
    print("  on call now:", result)


def main():
    print("=" * 60)
    print("Write Skew Demonstration")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as td:
        print("\n--- Scenario 1: Naive (no lock key) ---")
        path = os.path.join(td, "naive.wal")
        db = LedgerDB(path)
        setup(db)
        naive(db)
        db.close()

        print("\n--- Scenario 2: Fixed (shared lock key) ---")
        path = os.path.join(td, "fixed.wal")
        db = LedgerDB(path)
        setup(db)
        with_conflict(db)
        db.close()

    print("\n" + "=" * 60)
    print("Done.")


if __name__ == "__main__":
    main()
