"""Generic preference-graph engine for versioned structured documents."""

from __future__ import annotations

import itertools
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class ProjectAdapter(Protocol):
    """Interface implemented by a host repository."""

    preferences_path: Path

    def list_revisions(self) -> list[dict[str, Any]]: ...

    def available_profiles(self, revision: str) -> list[dict[str, Any]]: ...

    def load_version(self, revision: str, profile: str) -> dict[str, Any]: ...


def block_key(block: dict[str, Any]) -> str:
    return str(block.get("match_key") or block["id"])


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    first, second = sorted((left, right))
    return first, second


def has_path(edges: list[dict[str, Any]], start: str, target: str) -> bool:
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["better"], set()).add(edge["worse"])
    pending = [start]
    visited: set[str] = set()
    while pending:
        node = pending.pop()
        if node == target:
            return True
        if node in visited:
            continue
        visited.add(node)
        pending.extend(adjacency.get(node, ()))
    return False


class DecisionGraph:
    """Persist and analyze strict preferences and intentional incomparability."""

    def __init__(self, adapter: ProjectAdapter):
        self.adapter = adapter

    @property
    def path(self) -> Path:
        return Path(self.adapter.preferences_path)

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"schema_version": 1, "nodes": {}, "edges": [], "incomparables": []}

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self.empty()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            raise ValueError("unsupported preference graph schema")
        data.setdefault("nodes", {})
        data.setdefault("edges", [])
        data.setdefault("incomparables", [])
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="version-preferences.", suffix=".json", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def node_key(version: dict[str, Any]) -> str:
        return f"{version['revision']}:{version['profile']}:{version['content_hash'][:12]}"

    @staticmethod
    def node_record(version: dict[str, Any], order: int | None = None) -> dict[str, Any]:
        record = {
            "revision": version["revision"],
            "profile": version["profile"],
            "profile_label": version.get("profile_label", version["profile"]),
            "content_hash": version["content_hash"],
            "label": version["label"],
            "date": version.get("date", ""),
            "subject": version.get("subject", ""),
        }
        if order is not None:
            record["order"] = order
        return record

    def version_catalog(self) -> dict[str, dict[str, Any]]:
        nodes: dict[str, dict[str, Any]] = {}
        revisions = self.adapter.list_revisions()
        recordable_revisions = [item for item in revisions if item.get("recordable", True)]
        for order, revision in enumerate(recordable_revisions):
            for profile in self.adapter.available_profiles(str(revision["id"])):
                if not profile.get("recordable", True):
                    continue
                version = self.adapter.load_version(
                    str(revision["id"]), str(profile["id"])
                )
                if not version.get("recordable", True):
                    continue
                nodes[self.node_key(version)] = self.node_record(version, order)
        return nodes

    @staticmethod
    def loss_summary(better: dict[str, Any], worse: dict[str, Any]) -> dict[str, Any]:
        better_blocks = {block_key(block): block for block in better["blocks"]}
        worse_blocks = {block_key(block): block for block in worse["blocks"]}
        removed = [
            {
                "id": key,
                "section": block.get("section", ""),
                "text": block.get("text", ""),
            }
            for key, block in worse_blocks.items()
            if key not in better_blocks
        ]
        changed = [
            {
                "id": key,
                "section": worse_blocks[key].get("section", ""),
                "before": worse_blocks[key].get("text", ""),
                "after": better_blocks[key].get("text", ""),
            }
            for key in worse_blocks.keys() & better_blocks.keys()
            if worse_blocks[key].get("text") != better_blocks[key].get("text")
        ]
        added = sorted(better_blocks.keys() - worse_blocks.keys())
        return {"removed": removed, "changed": changed, "added_block_ids": added}

    @staticmethod
    def _incomparable_pairs(data: dict[str, Any]) -> set[tuple[str, str]]:
        return {
            canonical_pair(item["left"], item["right"])
            for item in data.get("incomparables", [])
        }

    @staticmethod
    def _resolved(
        edges: list[dict[str, Any]],
        incomparable: set[tuple[str, str]],
        left: str,
        right: str,
    ) -> bool:
        return (
            canonical_pair(left, right) in incomparable
            or has_path(edges, left, right)
            or has_path(edges, right, left)
        )

    def preference_graph(self) -> dict[str, Any]:
        data = self.load()
        nodes = self.version_catalog()
        for key, node in data["nodes"].items():
            nodes.setdefault(key, dict(node))
        edges = [
            edge for edge in data["edges"]
            if edge["better"] in nodes and edge["worse"] in nodes
        ]
        incomparables = [
            item for item in data["incomparables"]
            if item["left"] in nodes and item["right"] in nodes
        ]
        incomparable = self._incomparable_pairs({"incomparables": incomparables})

        incoming = {key: 0 for key in nodes}
        for edge in edges:
            incoming[edge["worse"]] += 1
        maximal = [key for key in nodes if incoming[key] == 0]
        maximal.sort(key=lambda key: (nodes[key].get("order", 10_000), nodes[key]["profile"]))

        suggestions: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def suggest(left: str, right: str, kind: str) -> None:
            pair = canonical_pair(left, right)
            if pair in seen or self._resolved(edges, incomparable, left, right):
                return
            seen.add(pair)
            suggestions.append({"left": left, "right": right, "kind": kind})

        by_profile: dict[str, list[str]] = {}
        for key in maximal:
            by_profile.setdefault(nodes[key]["profile"], []).append(key)
        for keys in by_profile.values():
            keys.sort(key=lambda key: nodes[key].get("order", 10_000))
            for index, left in enumerate(keys[:-1]):
                for right in keys[index + 1:]:
                    before = len(suggestions)
                    suggest(left, right, "revision-lineage")
                    if len(suggestions) > before:
                        break

        frontier = [
            min(keys, key=lambda key: nodes[key].get("order", 10_000))
            for keys in by_profile.values()
        ]
        frontier.sort(key=lambda key: nodes[key]["profile"])
        for left, right in itertools.combinations(frontier, 2):
            suggest(left, right, "cross-profile-frontier")

        return {
            "schema_version": data["schema_version"],
            "nodes": nodes,
            "edges": edges,
            "incomparables": incomparables,
            "maximal": maximal,
            "suggestions": suggestions,
            "counts": {
                "versions": len(nodes),
                "maximal": len(maximal),
                "strict_preferences": len(edges),
                "incomparable_pairs": len(incomparables),
                "unresolved_suggestions": len(suggestions),
            },
        }

    @staticmethod
    def _require_recordable(version: dict[str, Any]) -> None:
        if not version.get("recordable", True):
            raise ValueError("this source cannot be added to the durable preference graph")

    def add_preference(
        self,
        better_revision: str,
        better_profile: str,
        worse_revision: str,
        worse_profile: str,
        reason: str = "",
    ) -> dict[str, Any]:
        better = self.adapter.load_version(better_revision, better_profile)
        worse = self.adapter.load_version(worse_revision, worse_profile)
        self._require_recordable(better)
        self._require_recordable(worse)
        better_key = self.node_key(better)
        worse_key = self.node_key(worse)
        if better_key == worse_key:
            raise ValueError("a version cannot be preferred over itself")

        data = self.load()
        if has_path(data["edges"], worse_key, better_key):
            raise ValueError("this preference would create a cycle in the ordering graph")
        pair = canonical_pair(better_key, worse_key)
        data["incomparables"] = [
            item for item in data["incomparables"]
            if canonical_pair(item["left"], item["right"]) != pair
        ]
        for version, key in ((better, better_key), (worse, worse_key)):
            data["nodes"][key] = self.node_record(version)
        for edge in data["edges"]:
            if edge["better"] == better_key and edge["worse"] == worse_key:
                edge["reason"] = reason.strip()
                edge["updated_at"] = datetime.now(UTC).isoformat()
                self.save(data)
                return data
        data["edges"].append({
            "better": better_key,
            "worse": worse_key,
            "reason": reason.strip(),
            "created_at": datetime.now(UTC).isoformat(),
            "loss_summary": self.loss_summary(better, worse),
        })
        self.save(data)
        return data

    def add_incomparable(
        self,
        left_revision: str,
        left_profile: str,
        right_revision: str,
        right_profile: str,
        reason: str = "",
    ) -> dict[str, Any]:
        left = self.adapter.load_version(left_revision, left_profile)
        right = self.adapter.load_version(right_revision, right_profile)
        self._require_recordable(left)
        self._require_recordable(right)
        left_key = self.node_key(left)
        right_key = self.node_key(right)
        if left_key == right_key:
            raise ValueError("a version cannot be incomparable with itself")

        data = self.load()
        if has_path(data["edges"], left_key, right_key) or has_path(data["edges"], right_key, left_key):
            raise ValueError("these versions already have a strict preference relation")
        for version, key in ((left, left_key), (right, right_key)):
            data["nodes"][key] = self.node_record(version)
        pair = canonical_pair(left_key, right_key)
        for item in data["incomparables"]:
            if canonical_pair(item["left"], item["right"]) == pair:
                item["reason"] = reason.strip()
                item["updated_at"] = datetime.now(UTC).isoformat()
                self.save(data)
                return data
        data["incomparables"].append({
            "left": pair[0],
            "right": pair[1],
            "reason": reason.strip(),
            "created_at": datetime.now(UTC).isoformat(),
        })
        self.save(data)
        return data
