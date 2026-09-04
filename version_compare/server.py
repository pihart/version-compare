"""Serve the generic document comparison and preference-graph UI locally."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import mimetypes
import subprocess
import threading
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .core import DecisionGraph, ProjectAdapter


STATIC_ROOT = Path(__file__).resolve().parent / "static"
PDF_RENDER_LOCK = threading.Lock()


class Application:
    def __init__(self, adapter: ProjectAdapter):
        self.adapter = adapter
        self.decisions = DecisionGraph(adapter)
        self.generated_root = Path(
            getattr(adapter, "generated_root", Path.cwd() / ".build" / "version-compare-generated")
        )

    def render_visual(self, revision: str, profile: str) -> dict[str, Any]:
        version = self.adapter.load_version(revision, profile)
        resolver = getattr(self.adapter, "visual_path", None)
        if resolver is None:
            return {
                "available": False,
                "label": version["label"],
                "reason": "This project adapter does not provide exact visual artifacts",
                "pages": [],
            }
        path, reason = resolver(revision, profile)
        if path is None:
            return {"available": False, "label": version["label"], "reason": reason, "pages": []}
        path = Path(path)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        output_dir = self.generated_root / f"{profile}-{digest}"
        prefix = output_dir / "page"
        with PDF_RENDER_LOCK:
            pages = sorted(output_dir.glob("page-*.png")) if output_dir.is_dir() else []
            if not pages:
                output_dir.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-png", "-r", "120", str(path), str(prefix)],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                pages = sorted(output_dir.glob("page-*.png"))
        return {
            "available": True,
            "label": version["label"],
            "pages": [f"/generated/{output_dir.name}/{page.name}" for page in pages],
        }


def load_adapter(path: Path, project_root: Path) -> ProjectAdapter:
    resolved = path if path.is_absolute() else project_root / path
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"project adapter not found: {resolved}")
    spec = importlib.util.spec_from_file_location("version_compare_project_adapter", resolved)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load project adapter: {resolved}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    factory = getattr(module, "create_adapter", None)
    if not callable(factory):
        raise TypeError("project adapter must export create_adapter(project_root)")
    return factory(project_root)


def make_handler(application: Application) -> type[BaseHTTPRequestHandler]:
    class ComparisonHandler(BaseHTTPRequestHandler):
        server_version = "VersionCompare/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            print(f"[{self.log_date_time_string()}] {format % args}")

        def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def send_error_json(self, error: Exception, status: HTTPStatus) -> None:
            self.send_json({"error": str(error)}, status)

        def parsed_query(self) -> dict[str, str]:
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return {key: values[-1] for key, values in query.items() if values}

        def required_query(self, *names: str) -> tuple[str, ...]:
            query = self.parsed_query()
            missing = [name for name in names if not query.get(name)]
            if missing:
                raise ValueError(f"missing query parameter: {', '.join(missing)}")
            return tuple(query[name] for name in names)

        def do_GET(self) -> None:  # noqa: N802
            route = urllib.parse.urlsplit(self.path).path
            try:
                if route == "/api/catalog":
                    self.send_json({"revisions": application.adapter.list_revisions()})
                    return
                if route == "/api/profiles":
                    (revision,) = self.required_query("revision")
                    self.send_json({"profiles": application.adapter.available_profiles(revision)})
                    return
                if route == "/api/version":
                    revision, profile = self.required_query("revision", "profile")
                    self.send_json(application.adapter.load_version(revision, profile))
                    return
                if route == "/api/visual":
                    revision, profile = self.required_query("revision", "profile")
                    self.send_json(application.render_visual(revision, profile))
                    return
                if route == "/api/preferences":
                    self.send_json(application.decisions.preference_graph())
                    return
                self.serve_static(route)
            except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
                self.send_error_json(exc, HTTPStatus.BAD_REQUEST)

        def do_POST(self) -> None:  # noqa: N802
            route = urllib.parse.urlsplit(self.path).path
            try:
                body = self.read_json()
                if route == "/api/preferences":
                    required = ("better_revision", "better_profile", "worse_revision", "worse_profile")
                    missing = [key for key in required if not body.get(key)]
                    if missing:
                        raise ValueError(f"missing field(s): {', '.join(missing)}")
                    application.decisions.add_preference(
                        str(body["better_revision"]), str(body["better_profile"]),
                        str(body["worse_revision"]), str(body["worse_profile"]),
                        str(body.get("reason", "")),
                    )
                    self.send_json(application.decisions.preference_graph(), HTTPStatus.CREATED)
                    return
                if route == "/api/incomparables":
                    required = ("left_revision", "left_profile", "right_revision", "right_profile")
                    missing = [key for key in required if not body.get(key)]
                    if missing:
                        raise ValueError(f"missing field(s): {', '.join(missing)}")
                    application.decisions.add_incomparable(
                        str(body["left_revision"]), str(body["left_profile"]),
                        str(body["right_revision"]), str(body["right_profile"]),
                        str(body.get("reason", "")),
                    )
                    self.send_json(application.decisions.preference_graph(), HTTPStatus.CREATED)
                    return
                if route == "/api/refresh":
                    refresh = getattr(application.adapter, "refresh", None)
                    if callable(refresh):
                        refresh()
                    self.send_json({"revisions": application.adapter.list_revisions()})
                    return
                self.send_error_json(FileNotFoundError(route), HTTPStatus.NOT_FOUND)
            except (ValueError, OSError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
                self.send_error_json(exc, HTTPStatus.BAD_REQUEST)

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 64_000:
                raise ValueError("request is too large")
            raw = self.rfile.read(length)
            if not raw:
                return {}
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise ValueError("request body must be a JSON object")
            return parsed

        def serve_static(self, route: str) -> None:
            if route.startswith("/generated/"):
                relative = route.removeprefix("/generated/")
                root = application.generated_root
            else:
                relative = "index.html" if route in {"", "/"} else route.lstrip("/")
                root = STATIC_ROOT
            candidate = (root / relative).resolve()
            if root.resolve() not in candidate.parents:
                self.send_error_json(PermissionError("invalid static path"), HTTPStatus.FORBIDDEN)
                return
            if not candidate.is_file():
                self.send_error_json(FileNotFoundError(relative), HTTPStatus.NOT_FOUND)
                return
            body = candidate.read_bytes()
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                content_type = f"{content_type}; charset=utf-8"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return ComparisonHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise ValueError("the comparison server may only bind to localhost")
    project_root = args.project_root.resolve()
    application = Application(load_adapter(args.adapter, project_root))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(application))
    host, port = server.server_address
    url = f"http://{host}:{port}/"
    print(f"Version comparison: {url}")
    print("Press Ctrl-C to stop.")
    if args.open_browser:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping comparison server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
