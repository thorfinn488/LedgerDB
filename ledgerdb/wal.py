"""Write-ahead log.

A transaction's write records and its commit record are appended together and
fsynced once. On restart, only transactions with a commit record are replayed.
Anything else (a crash mid-commit, or a torn final line) is ignored.

Each line is one JSON object:
    {"t": "write",  "txn": <id>, "k": <key>, "v": <value>, "d": <deleted?>}
    {"t": "commit", "txn": <id>, "ts": <commit timestamp>}
"""
from __future__ import annotations

import json
import os


class WriteAheadLog:
    def __init__(self, path: str) -> None:
        self._file = open(path, "ab")

    def append_transaction(self, txn_id: str, commit_ts: int, writes: list[dict]) -> None:
        lines = [json.dumps(w) for w in writes]
        lines.append(json.dumps({"t": "commit", "txn": txn_id, "ts": commit_ts}))
        self._file.write(("\n".join(lines) + "\n").encode("utf-8"))
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        self._file.close()


def read_committed(path: str) -> tuple[list[tuple[int, list[dict]]], int]:
    """Replay the log.

    Returns (committed, valid_end):
      committed - [(commit_ts, write_records)] in commit order, for committed transactions only
      valid_end - byte offset just past the last complete, parseable line
    """
    committed: list[tuple[int, list[dict]]] = []
    pending: dict[str, list[dict]] = {}
    valid_end = 0
    if not os.path.exists(path):
        return committed, 0

    with open(path, "rb") as f:
        for line in f:
            if not line.endswith(b"\n"):
                break                       # torn final line from a crash
            try:
                record = json.loads(line)
            except ValueError:
                break
            valid_end += len(line)
            if record["t"] == "write":
                pending.setdefault(record["txn"], []).append(record)
            else:
                committed.append((record["ts"], pending.pop(record["txn"], [])))
    return committed, valid_end
