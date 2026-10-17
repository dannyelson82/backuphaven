"""Containers through the optional socket proxy (ADR-0005).

BackupHaven never touches the Docker socket. With ``linuxserver/socket-proxy``
configured to allow only container listing, start and stop
(``CONTAINERS=1``, ``ALLOW_START=1``, ``ALLOW_STOP=1``, everything else 0), this
client can do exactly three things: list containers, stop one, start one. It
refuses to stop BackupHaven itself or the proxy.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

TIMEOUT_S = 10
STOP_TIMEOUT_S = 90  # how long Docker waits before killing a container that won't stop
_ID = re.compile(r"^[0-9a-f]{12,64}$")
# Images BackupHaven never stops (itself and the proxy).
PROTECTED_IMAGES = ("backuphaven", "socket-proxy")


class ProxyError(Exception):
    """The proxy couldn't be reached or refused a request."""


@dataclass(frozen=True)
class Container:
    id: str
    name: str  # without the leading "/"
    image: str
    running: bool
    state: str  # running, exited, paused, restarting…

    @property
    def protected(self) -> bool:
        image = self.image.lower().split("@", 1)[0]
        return any(p in image for p in PROTECTED_IMAGES)


def base_url(address: str) -> str:
    """``tcp://socket-proxy:2375`` or ``http://…`` → ``http://socket-proxy:2375``."""
    parts = urlsplit(address.strip())
    if parts.scheme not in ("tcp", "http") or not parts.hostname:
        raise ProxyError(f"the proxy address {address!r} should look like tcp://socket-proxy:2375")
    return f"http://{parts.netloc}"


class DockerProxy:
    def __init__(self, address: str) -> None:
        self._base = base_url(address)

    def _request(self, method: str, path: str, timeout: float = TIMEOUT_S) -> Any:
        url = f"{self._base}{path}"  # always http:// (base_url)
        data = b"" if method == "POST" else None
        request = urllib.request.Request(url, method=method, data=data)  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                body = response.read(4 * 1024 * 1024)
        except urllib.error.HTTPError as exc:
            if exc.code == 304:  # already in that state
                return None
            detail = exc.read(500).decode("utf-8", "replace")
            if exc.code == 403:
                raise ProxyError(
                    "the socket proxy refused this; check it allows CONTAINERS, START and STOP"
                ) from None
            raise ProxyError(f"Docker answered {exc.code}: {detail.strip()[:200]}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ProxyError(f"can't reach the socket proxy: {reason}") from None
        return json.loads(body) if body else None

    def containers(self) -> list[Container]:
        items = self._request("GET", "/containers/json?all=1") or []
        out = []
        for item in items:
            names = [str(n).lstrip("/") for n in item.get("Names", [])]
            if not names or not _ID.fullmatch(str(item.get("Id", ""))):
                continue
            out.append(
                Container(
                    id=str(item["Id"]),
                    name=names[0],
                    image=str(item.get("Image", "")),
                    running=item.get("State") == "running",
                    state=str(item.get("State", "")),
                )
            )
        return out

    def find(self, name: str) -> Container | None:
        """The container named like an app's template (Unraid names them so)."""
        for container in self.containers():
            if container.name.lower() == name.lower():
                return container
        return None

    def stop(self, container: Container) -> None:
        if container.protected:
            raise ProxyError(f"BackupHaven never stops {container.name}")
        logger.info("stopping container", extra={"container": container.name})
        self._request(
            "POST",
            f"/containers/{container.id}/stop?t={STOP_TIMEOUT_S}",
            timeout=STOP_TIMEOUT_S + 30,
        )

    def start(self, container: Container) -> None:
        logger.info("starting container", extra={"container": container.name})
        self._request("POST", f"/containers/{container.id}/start", timeout=60)
