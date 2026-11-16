"""Web front end for LedgerDB: a browser UI that drives the real engine.

Run with:   python -m ledgerdb.web
Then open:  http://127.0.0.1:8000

Uses only the Python standard library. The page itself is ledgerdb/static/index.html.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .db import LedgerDB, WriteConflict

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_MISSING = object()


class ApiError(Exception):
    """A request the engine rejected. Sent to the browser as HTTP 400."""


class Session:
    """One database plus its open transactions, shared by all browser requests."""

    def __init__(self, wal_path: str, order: int = 3) -> None:
        self.wal_path = wal_path
        self.order = order
        self._lock = threading.Lock()
        self._count = 0
        self.txns: dict = {}
        self.db = LedgerDB(wal_path, order=order)

    def _txn(self, name):
        if not name or name not in self.txns:
            raise ApiError(f"no open transaction named {name!r}")
        return self.txns[name]

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or not key:
            raise ApiError("a key is required")
        return key

    def begin(self, name=None):
        with self._lock:
            if name:
                if name in self.txns:
                    raise ApiError(f"transaction {name} is already open")
            else:
                while True:
                    self._count += 1
                    name = f"T{self._count}"
                    if name not in self.txns:
                        break
            txn = self.db.begin()
            self.txns[name] = txn
            return {"name": name, "start_ts": txn.start_ts}

    def get(self, name, key):
        with self._lock:
            value = self._txn(name).get(self._key(key), _MISSING)
            return {"found": value is not _MISSING, "value": None if value is _MISSING else value}

    def put(self, name, key, value):
        with self._lock:
            self._txn(name).put(self._key(key), value)
            return {"ok": True}

    def delete(self, name, key):
        with self._lock:
            self._txn(name).delete(self._key(key))
            return {"ok": True}

    def scan(self, name, lo=None, hi=None):
        with self._lock:
            rows = self._txn(name).scan(lo or None, hi or None)
            return {"rows": [{"key": k, "value": v} for k, v in rows]}

    def commit(self, name):
        with self._lock:
            txn = self._txn(name)
            try:
                txn.commit()
            except WriteConflict as err:
                self.txns.pop(name, None)
                raise ApiError(f"write conflict, {name} aborted: {err}") from None
            self.txns.pop(name, None)
            return {"commit_ts": self.db.last_commit_ts}

    def rollback(self, name):
        with self._lock:
            self._txn(name).rollback()
            self.txns.pop(name, None)
            return {"ok": True}

    def history(self, key):
        with self._lock:
            return {"versions": [{"ts": ts, "value": v, "deleted": d} for ts, v, d in self.db.history(self._key(key))]}

    def restart(self):
        with self._lock:
            self.db.close()
            self.txns.clear()
            self.db = LedgerDB(self.wal_path, order=self.order)
            return {"commit_ts": self.db.last_commit_ts}

    def reset(self):
        with self._lock:
            self.db.close()
            self.txns.clear()
            self._count = 0
            if os.path.exists(self.wal_path):
                os.remove(self.wal_path)
            self.db = LedgerDB(self.wal_path, order=self.order)
            return {"ok": True}

    def state(self):
        with self._lock:
            wal = []
            if os.path.exists(self.wal_path):
                with open(self.wal_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                wal.append(json.loads(line))
                            except ValueError:
                                pass
            txns = [
                {"name": name, "start_ts": t.start_ts,
                 "pending": [{"key": k, "value": v, "deleted": d} for k, v, d in t.pending()]}
                for name, t in self.txns.items()
            ]
            return {
                "commit_ts": self.db.last_commit_ts,
                "order": self.order,
                "tree": self.db.tree_structure(),
                "keys": self.db.keys(),
                "txns": txns,
                "wal": wal[-300:],
                "wal_lines": len(wal),
            }

    def close(self):
        with self._lock:
            self.db.close()


class Handler(BaseHTTPRequestHandler):
    session: Session = None  # set on a subclass by make_server

    ACTIONS = {
        "/api/begin": lambda s, b: s.begin(b.get("name")),
        "/api/get": lambda s, b: s.get(b.get("txn"), b.get("key")),
        "/api/put": lambda s, b: s.put(b.get("txn"), b.get("key"), b.get("value")),
        "/api/delete": lambda s, b: s.delete(b.get("txn"), b.get("key")),
        "/api/scan": lambda s, b: s.scan(b.get("txn"), b.get("lo"), b.get("hi")),
        "/api/commit": lambda s, b: s.commit(b.get("txn")),
        "/api/rollback": lambda s, b: s.rollback(b.get("txn")),
        "/api/history": lambda s, b: s.history(b.get("key")),
        "/api/restart": lambda s, b: s.restart(),
        "/api/reset": lambda s, b: s.reset(),
    }

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _send(self, status, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, payload) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(200, self.session.state())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        action = self.ACTIONS.get(self.path)
        if action is None:
            return self._json(404, {"error": "unknown action"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json(400, {"error": "request body must be JSON"})
        try:
            self._json(200, action(self.session, body))
        except ApiError as err:
            self._json(400, {"error": str(err)})
        except (TypeError, ValueError, KeyError) as err:
            self._json(400, {"error": f"bad request: {err}"})


def make_server(wal_path: str, order: int = 3, port: int = 8000, host: str = "127.0.0.1"):
    session = Session(wal_path, order)
    handler = type("LedgerHandler", (Handler,), {"session": session})
    server = ThreadingHTTPServer((host, port), handler)
    server.session = session
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="LedgerDB web front end")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--wal", default=os.path.join("web_data", "ledger.wal"))
    parser.add_argument("--order", type=int, default=3, help="B+ tree order (max keys per node)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    options = parser.parse_args(argv)

    folder = os.path.dirname(options.wal)
    if folder:
        os.makedirs(folder, exist_ok=True)
    server = make_server(options.wal, options.order, options.port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"LedgerDB web front end running at {url}  (Ctrl+C to stop)")
    if not options.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
        server.session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
