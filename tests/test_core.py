from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from threading import Thread

from version_compare.core import DecisionGraph
from version_compare.server import Application, ThreadingHTTPServer, make_handler


class FakeAdapter:
    def __init__(self, root: Path):
        self.preferences_path = root / "decisions.json"
        self.generated_root = root / "generated"

    def list_revisions(self) -> list[dict]:
        return [
            {"id": "working", "short": "local", "date": "", "subject": "Working copy", "recordable": False},
            {"id": "r2", "short": "r2", "date": "2026-01-02", "subject": "Second revision"},
            {"id": "r1", "short": "r1", "date": "2026-01-01", "subject": "First revision"},
        ]

    def available_profiles(self, revision: str) -> list[dict]:
        return [{"id": "standard", "label": "Standard"}, {"id": "compact", "label": "Compact"}]

    def load_version(self, revision: str, profile: str) -> dict:
        text = f"Document {revision} in {profile} form"
        digest = hashlib.sha256(text.encode()).hexdigest()
        return {
            "revision": revision,
            "profile": profile,
            "profile_label": profile.title(),
            "label": f"{revision} · {profile.title()}",
            "date": "2026-01-02" if revision == "r2" else "2026-01-01",
            "subject": "Example revision",
            "content_hash": digest,
            "recordable": revision != "working",
            "blocks": [{
                "id": "overview",
                "match_key": "overview",
                "kind": "text",
                "section": "Overview",
                "text": text,
            }],
        }


class DecisionGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.adapter = FakeAdapter(Path(self.temporary.name))
        self.graph = DecisionGraph(self.adapter)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_catalog_preferences_and_incomparability(self) -> None:
        state = self.graph.preference_graph()
        self.assertEqual(state["counts"]["versions"], 4)
        self.assertEqual(state["counts"]["maximal"], 4)

        self.graph.add_incomparable("r2", "standard", "r2", "compact", "different uses")
        state = self.graph.preference_graph()
        self.assertEqual(state["counts"]["incomparable_pairs"], 1)

        self.graph.add_preference("r2", "standard", "r2", "compact", "more complete")
        state = self.graph.preference_graph()
        self.assertEqual(state["counts"]["incomparable_pairs"], 0)
        self.assertEqual(state["counts"]["strict_preferences"], 1)
        self.assertEqual(state["counts"]["maximal"], 3)

    def test_nonrecordable_sources_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be added"):
            self.graph.add_preference("working", "standard", "r2", "standard")

    def test_http_api_and_static_ui(self) -> None:
        application = Application(self.adapter)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(application))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(f"{base}/api/catalog") as response:
                catalog = json.load(response)
            self.assertEqual(catalog["revisions"][1]["id"], "r2")
            with urllib.request.urlopen(f"{base}/api/preferences") as response:
                state = json.load(response)
            self.assertEqual(state["counts"]["versions"], 4)
            with urllib.request.urlopen(base) as response:
                html = response.read()
            self.assertIn(b"Version comparison", html)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
