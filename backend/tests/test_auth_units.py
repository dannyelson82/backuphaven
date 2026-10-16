from ipaddress import ip_address
from pathlib import Path

import pytest

from backuphaven.auth import network, passwords, tokens
from backuphaven.auth.network import parse_networks, resolve_client_ip
from backuphaven.auth.throttle import FREE_ATTEMPTS, MAX_DELAY_S, LoginThrottle, delay_for

# --- network -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "private"),
    [
        ("10.1.2.3", True),
        ("172.16.0.1", True),
        ("172.31.255.255", True),
        ("172.32.0.1", False),
        ("192.168.1.10", True),
        ("127.0.0.1", True),
        ("::1", True),
        ("fd00::1", True),
        ("8.8.8.8", False),
        ("2001:db8::1", False),
        ("100.64.0.1", False),  # CGNAT/Tailscale: not in SECURITY.md's list
    ],
)
def test_private_ranges(ip: str, private: bool) -> None:
    parsed = network.parse_ip(ip)
    assert parsed is not None
    assert network.is_private(parsed) is private


def test_ipv4_mapped_ipv6_is_unwrapped() -> None:
    assert network.parse_ip("::ffff:192.168.1.5") == ip_address("192.168.1.5")


def test_garbage_ip_is_none() -> None:
    assert network.parse_ip("not-an-ip") is None
    assert network.parse_ip("") is None
    assert network.parse_ip(None) is None


def test_forwarded_for_ignored_without_trusted_proxy() -> None:
    ip = resolve_client_ip("203.0.113.9", "192.168.1.2", trusted=[])
    assert ip == ip_address("203.0.113.9")  # a spoofed header changes nothing


def test_forwarded_for_used_behind_trusted_proxy() -> None:
    trusted = parse_networks(["192.168.1.50"])
    ip = resolve_client_ip("192.168.1.50", "203.0.113.9", trusted)
    assert ip == ip_address("203.0.113.9")


def test_forwarded_for_takes_rightmost_untrusted_hop() -> None:
    # The client can prepend anything; only hops added by our proxies count.
    trusted = parse_networks(["192.168.1.50", "10.0.0.0/8"])
    ip = resolve_client_ip("192.168.1.50", "192.168.1.7, 203.0.113.9, 10.0.0.3", trusted)
    assert ip == ip_address("203.0.113.9")


@pytest.mark.parametrize("header", [None, "", "garbage", "203.0.113.9, garbage"])
def test_trusted_proxy_without_usable_header_is_unknown(header: str | None) -> None:
    # Never fall back to the proxy's own private address: that would grant the bypass.
    assert resolve_client_ip("192.168.1.50", header, parse_networks(["192.168.1.50"])) is None


def test_forwarded_https() -> None:
    trusted = parse_networks(["192.168.1.50"])
    assert network.forwarded_https("https", "1.2.3.4", None, [])
    assert network.forwarded_https("http", "192.168.1.50", "https", trusted)
    assert not network.forwarded_https("http", "192.168.1.60", "https", trusted)
    assert not network.forwarded_https("http", "192.168.1.50", None, trusted)


def test_bypass_rules() -> None:
    lan = ip_address("192.168.1.20")
    gateway = ip_address("172.17.0.1")
    gateways = frozenset({gateway})
    assert network.bypass_applies(lan, True, gateways)
    assert not network.bypass_applies(lan, False, gateways)
    assert not network.bypass_applies(gateway, True, gateways)  # ADR-0012
    assert not network.bypass_applies(ip_address("8.8.8.8"), True, gateways)
    assert not network.bypass_applies(None, True, gateways)


def test_read_default_gateways(tmp_path: Path) -> None:
    (tmp_path / "route").write_text(
        "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
        "eth0\t00000000\t010011AC\t0003\t0\t0\t0\t00000000\n"
        "eth0\t000011AC\t00000000\t0001\t0\t0\t0\t0000FFFF\n"
    )
    (tmp_path / "ipv6_route").write_text(
        "00000000000000000000000000000000 00 00000000000000000000000000000000 00 "
        "fe800000000000000000000000000001 00000400 00000001 00000000 00000003 eth0\n"
    )
    assert network.read_default_gateways(tmp_path) == {
        ip_address("172.17.0.1"),
        ip_address("fe80::1"),
    }


def test_read_default_gateways_missing_files(tmp_path: Path) -> None:
    assert network.read_default_gateways(tmp_path) == frozenset()


# --- throttle ----------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_delay_grows_exponentially_and_caps() -> None:
    assert [delay_for(n) for n in range(FREE_ATTEMPTS)] == [0.0] * FREE_ATTEMPTS
    assert delay_for(FREE_ATTEMPTS) == 2.0
    assert delay_for(FREE_ATTEMPTS + 1) == 4.0
    assert delay_for(100) == MAX_DELAY_S


def test_throttle_blocks_after_free_attempts_then_releases() -> None:
    clock = FakeClock()
    throttle = LoginThrottle(clock)
    keys = ["user:a", "ip:1.2.3.4"]
    for _ in range(FREE_ATTEMPTS - 1):
        assert throttle.failure(keys) == []
        assert throttle.retry_after(keys) == 0
    assert throttle.failure(keys) == keys  # newly throttled: audit-logged by the caller
    assert throttle.retry_after(keys) == 2.0
    clock.now += 2.0
    assert throttle.retry_after(keys) == 0


def test_throttle_is_per_key() -> None:
    throttle = LoginThrottle(FakeClock())
    for _ in range(FREE_ATTEMPTS):
        throttle.failure(["user:a", "ip:1.1.1.1"])
    assert throttle.retry_after(["user:b", "ip:2.2.2.2"]) == 0
    assert throttle.retry_after(["user:b", "ip:1.1.1.1"]) > 0  # same IP, other username
    assert throttle.retry_after(["user:a", "ip:2.2.2.2"]) > 0  # same username, other IP


def test_success_clears() -> None:
    throttle = LoginThrottle(FakeClock())
    for _ in range(FREE_ATTEMPTS):
        throttle.failure(["user:a"])
    throttle.success(["user:a"])
    assert throttle.retry_after(["user:a"]) == 0


# --- passwords and tokens ----------------------------------------------------


def test_password_hash_round_trip() -> None:
    hashed = passwords.hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert passwords.verify_password(hashed, "correct horse battery")
    assert not passwords.verify_password(hashed, "wrong password")
    assert not passwords.verify_password("not-a-hash", "anything")


def test_tokens_are_random_and_hashed() -> None:
    a, b = tokens.new_session_token(), tokens.new_session_token()
    assert a != b
    assert len(tokens.new_api_key()) == 64  # 32 bytes, hex
    assert tokens.hash_token(a) != a
    assert len(tokens.hash_token(a)) == 64
    assert tokens.tokens_equal("x", "x")
    assert not tokens.tokens_equal("x", "y")


def test_same_origin() -> None:
    def ok(
        origin: str | None, host: str | None, peer: str = "192.168.1.5", fwd: str | None = None
    ) -> bool:
        return network.same_origin(
            origin, host, peer, fwd, network.parse_networks(["172.17.0.0/16"])
        )

    assert ok("http://tower:7171", "tower:7171")
    assert ok("http://Tower:7171", "tower:7171")
    assert ok("https://backuphaven.example", "backuphaven.example:443")  # default port
    assert ok("http://[::1]:7171", "[::1]:7171")
    assert not ok("http://evil.example", "tower:7171")
    assert not ok("http://tower:7172", "tower:7171")
    assert not ok("https://tower:7171", "tower:7171:443")
    assert not ok(None, "tower:7171")
    assert not ok("null", "tower:7171")  # sandboxed iframes, file:// pages
    assert not ok("ftp://tower:7171", "tower:7171")
    assert not ok("http://tower:7171/path", "tower:7171")
    assert not ok("http://tower:notaport", "tower:notaport")
    assert not ok("http://tower:7171", None)
    # Behind a trusted proxy that rewrites Host, X-Forwarded-Host counts...
    proxied = "https://backuphaven.example"
    assert ok(proxied, "172.17.0.1:7171", peer="172.17.0.2", fwd="backuphaven.example")
    # ...but not from anyone else.
    assert not ok(proxied, "tower:7171", peer="192.168.1.5", fwd="backuphaven.example")
