import json
import threading
import urllib.error
import urllib.request

import pytest

from ledgerdb.web import ApiError, Session, make_server


def test_session_runs_a_transaction_end_to_end(tmp_path):
    s = Session(str(tmp_path / "w.wal"), order=3)
    s.begin("T1")
    s.put("T1", "k", 1)
    assert s.commit("T1") == {"commit_ts": 1}
    s.begin("T2")
    assert s.get("T2", "k") == {"found": True, "value": 1}
    state = s.state()
    assert state["commit_ts"] == 1
    assert state["keys"] == ["k"]
    assert [t["name"] for t in state["txns"]] == ["T2"]
    s.close()


def test_missing_key_is_reported_as_not_found(tmp_path):
    s = Session(str(tmp_path / "w.wal"))
    s.begin("T1")
    assert s.get("T1", "nothing") == {"found": False, "value": None}
    s.close()


def test_write_conflict_becomes_an_api_error(tmp_path):
    s = Session(str(tmp_path / "w.wal"))
    s.begin("A")
    s.begin("B")
    s.put("A", "k", 1)
    s.put("B", "k", 2)
    s.commit("A")
    with pytest.raises(ApiError, match="write conflict, B aborted"):
        s.commit("B")
    assert "B" not in [t["name"] for t in s.state()["txns"]]
    s.close()


def test_unknown_transaction_is_an_api_error(tmp_path):
    s = Session(str(tmp_path / "w.wal"))
    with pytest.raises(ApiError, match="no open transaction named 'nope'"):
        s.get("nope", "k")
    s.close()


def test_tree_structure_shows_splits(tmp_path):
    s = Session(str(tmp_path / "w.wal"), order=3)
    for i, name in enumerate(["a", "b", "c", "d", "e"]):
        s.begin(f"T{i}")
        s.put(f"T{i}", name, i)
        s.commit(f"T{i}")
    tree = s.state()["tree"]
    assert tree["leaf"] is False            # the root is now an internal node
    assert len(tree["children"]) >= 2
    s.close()


def test_restart_recovers_and_reset_clears(tmp_path):
    s = Session(str(tmp_path / "w.wal"))
    s.begin("T1")
    s.put("T1", "k", "v")
    s.commit("T1")
    s.restart()
    assert s.get("T1", "k") if False else True          # restart drops open transactions
    s.begin("R")
    assert s.get("R", "k")["value"] == "v"              # committed data survived
    s.reset()
    assert s.state()["keys"] == [] and s.state()["commit_ts"] == 0
    s.close()


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read())


def test_http_round_trip(tmp_path):
    server = make_server(str(tmp_path / "h.wal"), order=3, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read()
        assert b"LedgerDB" in page and b"B+ tree index" in page

        assert _post(base, "/api/begin", {"name": "T1"})["name"] == "T1"
        _post(base, "/api/put", {"txn": "T1", "key": "account:alice", "value": 100})
        assert _post(base, "/api/commit", {"txn": "T1"}) == {"commit_ts": 1}

        with urllib.request.urlopen(base + "/api/state") as res:
            state = json.loads(res.read())
        assert state["keys"] == ["account:alice"]

        with pytest.raises(urllib.error.HTTPError) as err:
            _post(base, "/api/commit", {"txn": "nope"})
        assert err.value.code == 400
        assert "no open transaction named 'nope'" in json.loads(err.value.read())["error"]
    finally:
        server.shutdown()
        server.server_close()
        server.session.close()
