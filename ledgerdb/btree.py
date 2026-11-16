"""A B+ tree mapping keys to values, with point lookups and ordered range scans.

Keys are never removed from the tree. LedgerDB records deletions as versions,
so the tree only needs insertion and node splitting.
"""
from __future__ import annotations

import bisect


class _Leaf:
    __slots__ = ("keys", "values", "next")

    def __init__(self) -> None:
        self.keys: list = []
        self.values: list = []
        self.next: _Leaf | None = None  # links leaves in key order for range scans


class _Internal:
    __slots__ = ("keys", "children")

    def __init__(self) -> None:
        self.keys: list = []        # separators; children[i] holds keys < keys[i]
        self.children: list = []    # always len(keys) + 1 children


class BPlusTree:
    def __init__(self, order: int = 8) -> None:
        if order < 3:
            raise ValueError("order must be at least 3")
        self.order = order           # maximum keys per node
        self.root: _Leaf | _Internal = _Leaf()
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def get(self, key, default=None):
        leaf = self._find_leaf(key)
        i = bisect.bisect_left(leaf.keys, key)
        if i < len(leaf.keys) and leaf.keys[i] == key:
            return leaf.values[i]
        return default

    def put(self, key, value) -> None:
        split = self._insert(self.root, key, value)
        if split is not None:
            separator, right = split
            new_root = _Internal()
            new_root.keys = [separator]
            new_root.children = [self.root, right]
            self.root = new_root

    def scan(self, lo=None, hi=None):
        """Yield (key, value) with lo <= key < hi in key order. None means unbounded."""
        if lo is None:
            leaf, start = self._leftmost_leaf(), 0
        else:
            leaf = self._find_leaf(lo)
            start = bisect.bisect_left(leaf.keys, lo)
        while leaf is not None:
            for i in range(start, len(leaf.keys)):
                key = leaf.keys[i]
                if hi is not None and key >= hi:
                    return
                yield key, leaf.values[i]
            leaf = leaf.next
            start = 0

    def dump(self) -> str:
        """Return a text picture of the tree, one node per line, indented by depth."""
        lines: list[str] = []

        def walk(node, depth: int) -> None:
            pad = "  " * depth
            if isinstance(node, _Leaf):
                lines.append(f"{pad}leaf {node.keys}")
            else:
                lines.append(f"{pad}node {node.keys}")
                for child in node.children:
                    walk(child, depth + 1)

        walk(self.root, 0)
        return "\n".join(lines)

    def structure(self) -> dict:
        """The tree as nested dicts: {"leaf": bool, "keys": [...], "children": [...]}."""

        def walk(node):
            if isinstance(node, _Leaf):
                return {"leaf": True, "keys": list(node.keys)}
            return {"leaf": False, "keys": list(node.keys), "children": [walk(c) for c in node.children]}

        return walk(self.root)

    def check_invariants(self) -> None:
        """Raise AssertionError if the tree structure is broken. Used by the tests."""

        def walk(node, lo, hi) -> None:
            if isinstance(node, _Leaf):
                assert node.keys == sorted(node.keys), "leaf keys out of order"
                assert len(set(node.keys)) == len(node.keys), "duplicate key in leaf"
                for key in node.keys:
                    assert (lo is None or key >= lo) and (hi is None or key < hi), "key outside bounds"
                return
            assert len(node.children) == len(node.keys) + 1, "wrong child count"
            assert node.keys == sorted(node.keys), "separators out of order"
            bounds = [lo] + node.keys + [hi]
            for i, child in enumerate(node.children):
                walk(child, bounds[i], bounds[i + 1])

        walk(self.root, None, None)

    # ----- internals -------------------------------------------------

    def _find_leaf(self, key) -> _Leaf:
        node = self.root
        while isinstance(node, _Internal):
            node = node.children[bisect.bisect_right(node.keys, key)]
        return node

    def _leftmost_leaf(self) -> _Leaf:
        node = self.root
        while isinstance(node, _Internal):
            node = node.children[0]
        return node

    def _insert(self, node, key, value):
        """Insert below node. Return (separator, new_right_node) if node split, else None."""
        if isinstance(node, _Leaf):
            i = bisect.bisect_left(node.keys, key)
            if i < len(node.keys) and node.keys[i] == key:
                node.values[i] = value
                return None
            node.keys.insert(i, key)
            node.values.insert(i, value)
            self._size += 1
            return self._split_leaf(node) if len(node.keys) > self.order else None

        i = bisect.bisect_right(node.keys, key)
        split = self._insert(node.children[i], key, value)
        if split is None:
            return None
        separator, right = split
        node.keys.insert(i, separator)
        node.children.insert(i + 1, right)
        return self._split_internal(node) if len(node.keys) > self.order else None

    def _split_leaf(self, leaf: _Leaf):
        mid = len(leaf.keys) // 2
        right = _Leaf()
        right.keys = leaf.keys[mid:]
        right.values = leaf.values[mid:]
        del leaf.keys[mid:]
        del leaf.values[mid:]
        right.next = leaf.next
        leaf.next = right
        return right.keys[0], right

    def _split_internal(self, node: _Internal):
        mid = len(node.keys) // 2
        separator = node.keys[mid]          # moves up; not kept in either half
        right = _Internal()
        right.keys = node.keys[mid + 1:]
        right.children = node.children[mid + 1:]
        del node.keys[mid:]
        del node.children[mid + 1:]
        return separator, right
