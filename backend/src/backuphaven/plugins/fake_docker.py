"""A fake socket proxy speaking a little of the Docker Engine API: for tests and
for plugin fixtures of stop-mode plugins (no real Docker needed)."""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


@dataclass
class FakeDocker:
    containers: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> info
    calls: list[str] = field(default_factory=list)
    refuse: bool = False  # answer 403 like a proxy without START/STOP
    fail_start: bool = False  # starting "works" but the container stays stopped

    def add(self, name: str, image: str, running: bool = True) -> str:
        cid = f"{abs(hash(name)):012x}"[:12].ljust(12, "0")
        self.containers[name] = {
            "Id": cid,
            "Names": [f"/{name}"],
            "Image": image,
            "State": "running" if running else "exited",
        }
        return cid

    def state(self, name: str) -> str:
        return str(self.containers[name]["State"])


@contextmanager
def fake_docker() -> Iterator[tuple[FakeDocker, str]]:
    docker = FakeDocker()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: Any = None) -> None:
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            docker.calls.append(f"GET {self.path}")
            if self.path.startswith("/containers/json"):
                self._send(200, list(docker.containers.values()))
            else:
                self._send(403, {"message": "forbidden"})

        def do_POST(self) -> None:
            docker.calls.append(f"POST {self.path}")
            if docker.refuse:
                self._send(403, {"message": "forbidden"})
                return
            parts = self.path.split("?")[0].strip("/").split("/")
            if len(parts) == 3 and parts[0] == "containers" and parts[2] in ("start", "stop"):
                for info in docker.containers.values():
                    if info["Id"] == parts[1]:
                        new = "running" if parts[2] == "start" else "exited"
                        if parts[2] == "start" and docker.fail_start:
                            new = "exited"
                        if info["State"] == new:
                            self._send(304)
                            return
                        info["State"] = new
                        self._send(204)
                        return
                self._send(404, {"message": "no such container"})
                return
            self._send(403, {"message": "forbidden"})

        def log_message(self, *_args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield docker, f"tcp://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
