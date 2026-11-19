# LedgerDB

**A Key-Value Database Engine Without SQL**

> ADBMS PBL Project — Chandigarh University  
> Ayush (UID: 24BCF10097) — B.E. (CSE) F.S.D

---

## Overview

LedgerDB is a durable key-value database engine written in Python that demonstrates the internals behind several ADBMS topic
:

- **B+ tree index** — ordered point lookups and range scans
- **Write-ahead log (WAL)** — crash recovery; only committed transactions survive a restart
- **Snapshot isolation (MVCC)** — readers never block writers; each transaction sees a consistent snapshot
- **First-committer-wins conflict detection** — prevents lost updates
- **Write skew demonstration** — shows an isolation anomaly and how to prevent it

No SQL. No query language. Applications use it through transactions and a key-value API.

## Project Structure

```
ledgerdb/
├── __init__.py          # public API: LedgerDB, WriteConflict, Transaction
├── btree.py             # B+ tree index (insert-only, linked leaves)
├── wal.py               # write-ahead log (JSON-lines, fsync per commit)
├── db.py                # database engine (MVCC, snapshot isolation, conflict detection)
├── web.py               # browser-based interactive front end
└── static/
    └── index.html       # single-page UI for guided demos
tests/
├── test_btree.py        # B+ tree tests (random inserts, invariants, scans)
├── test_db.py           # database tests (isolation, recovery, conflicts)
└── test_web.py          # web front-end tests (Session API + HTTP)
examples/
└── write_skew_demo.py   # runnable write skew demonstration
```

## Quick Start

### Install

```bash
pip install -e .
```

### Transfer Example

```python
from ledgerdb import LedgerDB, WriteConflict

db = LedgerDB("data.wal")

with db.transaction() as t:
    t.put("account:alice", 100)
    t.put("account:bob", 50)

def transfer(txn, src, dst, amount):
    txn.put(src, txn.get(src) - amount)
    txn.put(dst, txn.get(dst) + amount)

db.run(lambda t: transfer(t, "account:alice", "account:bob", 30))

print(db.begin().scan("account:", "account;"))   # sorted range scan
# [('account:alice', 70), ('account:bob', 80)]
db.close()
```

### Run the Web Front End

```bash
python -m ledgerdb.web
```

Then open http://127.0.0.1:8000. The UI has six guided demos:

1. **Snapshot isolation** — a reader keeps seeing an old value while a newer commit changes it
2. **Write conflict** — two transactions write the same key; the second commit is rejected
3. **Write skew (naive)** — two transactions break a rule by writing different keys
4. **Write skew (fixed)** — a shared lock key forces a conflict and preserves the rule
5. **Crash & recovery** — uncommitted writes are lost; committed data survives
6. **B+ tree growth** — watch the tree split as keys are inserted one by one

### Run the Write Skew Demo

```bash
python examples/write_skew_demo.py
```

### Run Tests

```bash
pytest -v
```

All 18 tests should pass.

## Key API

```python
from ledgerdb import LedgerDB, WriteConflict

db = LedgerDB("data.wal")

# manual transaction
txn = db.begin()
txn.put("key", "value")
txn.get("key")               # "value"
txn.delete("key")
txn.scan("a", "z")           # [(key, value), ...] in [a, z)
txn.commit()                  # or txn.rollback()

# context manager (auto-commit / rollback)
with db.transaction() as t:
    t.put("x", 42)

# auto-retry on conflict
db.run(lambda t: t.put("x", t.get("x") + 1))

# introspection
db.keys()                     # all keys ever written, sorted
db.history("key")             # [(ts, value, deleted), ...]
db.tree_dump()                # text picture of the B+ tree

db.close()
```

## Design Highlights

| Component | Description |
|-----------|-------------|
| **B+ tree** | Insert-only; leaves linked for range scans; order configurable |
| **WAL** | JSON-lines; write records + commit record per txn; one `fsync` per commit |
| **MVCC** | Each write creates a timestamped version; readers pick the version their snapshot allows |
| **Conflict detection** | First-committer-wins on per-key basis |
| **Recovery** | Replay only committed txns; torn tail truncated; uncommitted writes discarded |

## Limitations

- Old versions are never removed (memory/log grow without bound)
- No checkpoint or log compaction
- Snapshot isolation only (write skew possible without application-level mitigation)
- Single-process only (thread-safe via one lock)
- Keys must be strings; values must be JSON-serializable

## References

1. Elmasri, R., and Navathe, S. B. *Fundamentals of Database Systems*. Pearson Education.
2. Silberschatz, A., Korth, H. F., and Sudarshan, S. *Database System Concepts*. McGraw-Hill.
3. Ramakrishnan, R., and Gehrke, J. *Database Management Systems*, 3rd ed. McGraw-Hill.
4. Gray, J., and Reuter, A. *Transaction Processing: Concepts and Techniques*. Morgan Kaufmann.
5. Bernstein, P. A., and Newcomer, E. *Principles of Transaction Processing*, 2nd ed. Morgan Kaufmann.
