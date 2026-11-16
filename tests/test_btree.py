"""Tests for the B+ tree index (ledgerdb/btree.py).

Covers random insertion against a reference dictionary, structural invariants,
range scans, overwrite semantics, and parameter validation.
"""
import random

import pytest

from ledgerdb.btree import BPlusTree


@pytest.mark.parametrize("order", [3, 4, 8])
def test_random_inserts_match_a_dict(order):
    """Insert 2000 random keys and verify every lookup against a plain dict."""
    tree = BPlusTree(order=order)
    ref = {}
    keys = [f"k{i:04d}" for i in range(2000)]
    random.shuffle(keys)
    for key in keys:
        value = random.randint(0, 10_000)
        tree.put(key, value)
        ref[key] = value
    tree.check_invariants()
    assert len(tree) == len(ref)
    for key, expected in ref.items():
        assert tree.get(key) == expected, f"mismatch on {key}"


@pytest.mark.parametrize("order", [3, 4, 8])
def test_structure_after_inserts(order):
    """After 2000 inserts the tree passes its own invariant checker."""
    tree = BPlusTree(order=order)
    for i in range(2000):
        tree.put(f"k{i:04d}", i)
    tree.check_invariants()


def test_range_scan_half_open():
    """scan(lo, hi) returns keys in [lo, hi)."""
    tree = BPlusTree(order=3)
    for c in "abcdefghij":
        tree.put(c, ord(c))
    result = list(tree.scan("c", "g"))
    assert [k for k, _ in result] == ["c", "d", "e", "f"]


def test_full_scan():
    """scan() with no bounds yields all keys in order."""
    tree = BPlusTree(order=4)
    keys = list("gdbface")
    for k in keys:
        tree.put(k, k)
    scanned = [k for k, _ in tree.scan()]
    assert scanned == sorted(keys)


def test_overwrite_keeps_size():
    """Overwriting an existing key does not increase the tree's size."""
    tree = BPlusTree(order=3)
    tree.put("x", 1)
    tree.put("x", 2)
    assert len(tree) == 1
    assert tree.get("x") == 2


def test_get_missing_returns_default():
    tree = BPlusTree(order=3)
    assert tree.get("nope") is None
    assert tree.get("nope", 42) == 42


def test_order_below_three_is_rejected():
    with pytest.raises(ValueError, match="order must be at least 3"):
        BPlusTree(order=2)


def test_dump_returns_string():
    tree = BPlusTree(order=3)
    for c in "abcd":
        tree.put(c, c)
    text = tree.dump()
    assert isinstance(text, str)
    assert "leaf" in text


def test_structure_returns_dict():
    tree = BPlusTree(order=3)
    for c in "abcde":
        tree.put(c, c)
    s = tree.structure()
    assert "keys" in s
    assert isinstance(s["keys"], list)
