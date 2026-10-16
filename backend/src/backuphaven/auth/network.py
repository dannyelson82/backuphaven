"""Client address resolution and the local-address bypass rules.

SECURITY.md: the client IP comes from the socket, never from
``X-Forwarded-For``, unless the immediate peer is a configured trusted proxy.
ReelHaven ADR-0012: the container's Docker gateway never gets the bypass.
"""

import logging
from collections.abc import Iterable
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network

PRIVATE_NETWORKS: tuple[IPNetwork, ...] = tuple(
    ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "fc00::/7", "::1/128")
)


def parse_ip(value: str | None) -> IPAddress | None:
    if not value:
        return None
    try:
        ip = ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(ip, IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped  # ::ffff:192.168.1.5 is really 192.168.1.5
    return ip


def parse_networks(values: Iterable[str]) -> list[IPNetwork]:
    """Parse CIDRs or single addresses; raises ValueError on bad input."""
    return [ip_network(v.strip(), strict=False) for v in values]


def _in_any(ip: IPAddress, networks: Iterable[IPNetwork]) -> bool:
    return any(ip.version == n.version and ip in n for n in networks)


def is_private(ip: IPAddress) -> bool:
    return _in_any(ip, PRIVATE_NETWORKS)


def resolve_client_ip(
    socket_ip: str | None, forwarded_for: str | None, trusted: list[IPNetwork]
) -> IPAddress | None:
    """The real client address, or None if it can't be determined safely.

    Behind trusted proxies, walk ``X-Forwarded-For`` from the right and return
    the first address that isn't a trusted proxy. If the header is missing or
    malformed, return None rather than the proxy's own (private) address, which
    would otherwise qualify for the local-address bypass.
    """
    peer = parse_ip(socket_ip)
    if peer is None or not _in_any(peer, trusted):
        return peer
    for hop in reversed((forwarded_for or "").split(",")):
        ip = parse_ip(hop)
        if ip is None:
            return None
        if not _in_any(ip, trusted):
            return ip
    return None


def forwarded_https(
    scheme: str, socket_ip: str | None, forwarded_proto: str | None, trusted: list[IPNetwork]
) -> bool:
    if scheme == "https":
        return True
    peer = parse_ip(socket_ip)
    if peer is None or not _in_any(peer, trusted) or not forwarded_proto:
        return False
    return forwarded_proto.split(",")[-1].strip().lower() == "https"


def bypass_applies(ip: IPAddress | None, enabled: bool, gateways: frozenset[IPAddress]) -> bool:
    return enabled and ip is not None and ip not in gateways and is_private(ip)


def read_default_gateways(proc: Path = Path("/proc/net")) -> frozenset[IPAddress]:
    """Default gateway addresses of this container (Linux only)."""
    gateways: set[IPAddress] = set()
    try:
        for line in (proc / "route").read_text().splitlines()[1:]:
            fields = line.split()
            # Destination 0.0.0.0 with the RTF_GATEWAY flag (0x2).
            if len(fields) > 3 and fields[1] == "00000000" and int(fields[3], 16) & 0x2:
                gateways.add(IPv4Address(bytes.fromhex(fields[2])[::-1]))
    except (OSError, ValueError):
        logger.debug("no IPv4 route table available")
    try:
        for line in (proc / "ipv6_route").read_text().splitlines():
            fields = line.split()
            if len(fields) > 4 and fields[0] == "0" * 32 and fields[1] == "00":
                hop = IPv6Address(bytes.fromhex(fields[4]))
                if not hop.is_unspecified:
                    gateways.add(hop)
    except (OSError, ValueError):
        logger.debug("no IPv6 route table available")
    return frozenset(gateways)


def _host_port(value: str, scheme: str) -> str | None:
    """``host[:port]`` in lower case, without the scheme's default port."""
    try:
        parts = urlsplit(f"{scheme}://{value.strip()}")
        host, port = parts.hostname, parts.port
    except ValueError:
        return None
    if not host:
        return None
    if port is None or (scheme, port) in (("http", 80), ("https", 443)):
        return host
    return f"{host}:{port}"


def same_origin(
    origin: str | None,
    host: str | None,
    socket_ip: str | None,
    forwarded_host: str | None,
    trusted: list[IPNetwork],
) -> bool:
    """Whether a WebSocket's ``Origin`` is this site (SECURITY.md).

    Browsers send ``Origin`` with every WebSocket handshake, and cookies go
    along cross-site too, so this is what stops another website from opening a
    socket with the user's session. ``X-Forwarded-Host`` counts only from a
    trusted proxy.
    """
    if not origin or origin == "null":
        return False
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or parsed.path not in ("", "/"):
        return False
    wanted = _host_port(parsed.netloc, parsed.scheme)
    if wanted is None:
        return False
    candidates = [host]
    peer = parse_ip(socket_ip)
    if forwarded_host and peer is not None and _in_any(peer, trusted):
        candidates.append(forwarded_host.split(",")[-1])
    return any(c and _host_port(c, parsed.scheme) == wanted for c in candidates)
