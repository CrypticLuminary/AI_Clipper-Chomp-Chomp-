"""HTTPS transport with DNS pinning for untrusted creator URLs."""
from __future__ import annotations

from contextlib import contextmanager
from http.client import HTTPSConnection, HTTPException
from ipaddress import ip_address
import json
import re
import socket
from time import monotonic, time
from urllib.parse import urlencode, urljoin, urlsplit

from .source import BridgeError


PLATFORM_DOMAINS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "googlevideo.com", "ytimg.com",
                    "youtube.googleapis.com", "twitch.tv", "ttvnw.net", "twitchcdn.net", "jtvnw.net")
TWITCH_DOWNLOAD_DOMAINS = ("twitch.tv", "ttvnw.net", "twitchcdn.net", "jtvnw.net", "cloudfront.net")
REDIRECT = {301, 302, 303, 307, 308}


def domain_matches(host: str, domains) -> bool:
    return any(host == name or host.endswith("." + name) for name in domains)


def media_target(url: str, *, native: bool = False):
    try:
        if len(url) > 8192 or "\\" in url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
            raise ValueError()
        p = urlsplit(url)
        host = (p.hostname or "").rstrip(".")
        if p.scheme != "https" or not host or p.username is not None or p.password is not None \
                or p.port not in {None, 443} or p.fragment or not re.fullmatch(r"[A-Za-z0-9.:-]+", host):
            raise ValueError()
    except ValueError:
        raise BridgeError("unsafe_media_url", "An absolute HTTPS media URL on port 443 is required.") from None
    if native and not domain_matches(host, TWITCH_DOWNLOAD_DOMAINS):
        raise BridgeError("unsafe_media_url", "The Twitch download host is outside the supported CDN set.")
    if not native and domain_matches(host, PLATFORM_DOMAINS):
        raise BridgeError("media_unavailable", "Use an independently supplied creator original or feed for this source.")
    return p, host


def public_addresses(host: str):
    try:
        rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except OSError:
        raise BridgeError("network_error", "Media hostname could not be resolved.") from None
    if not rows:
        raise BridgeError("network_error", "Media hostname could not be resolved.")
    for family, _, _, _, address in rows:
        ip = ip_address(address[0])
        effective = getattr(ip, "ipv4_mapped", None) or ip
        if (family not in {socket.AF_INET, socket.AF_INET6} or not effective.is_global or effective.is_multicast
                or effective.is_reserved or effective.is_loopback or effective.is_link_local
                or getattr(ip, "scope_id", None) or getattr(ip, "sixtofour", None)
                or getattr(ip, "teredo", None) or (ip.version == 6 and
                    (str(ip).startswith("64:ff9b:") or str(ip).startswith("64:ff9b:1:")))):
            raise BridgeError("unsafe_media_url", "Media DNS resolved outside the public unicast Internet.")
    return rows


class PinnedHTTPS(HTTPSConnection):
    def __init__(self, host, addresses, timeout):
        super().__init__(host, 443, timeout=timeout)
        self.addresses = addresses

    def connect(self):
        deadline = monotonic() + self.timeout
        for family, socktype, proto, _, address in self.addresses:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            raw = socket.socket(family, socktype, proto)
            raw.settimeout(remaining)
            try:
                raw.connect(address)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError:
                raw.close()
        raise OSError("No validated HTTPS address could be reached")


def _request_path(p):
    return (p.path or "/") + ("?" + p.query if p.query else "")


@contextmanager
def open_media(url: str, *, timeout: float, native: bool = False):
    """Validate each hop and keep TLS verification on the original hostname."""
    for _ in range(5):
        p, host = media_target(url, native=native)
        con = PinnedHTTPS(host, public_addresses(host), timeout)
        try:
            con.request("GET", _request_path(p), headers={"Accept-Encoding": "identity", "User-Agent": "Chomp-Chomp/StreamBridge"})
            response = con.getresponse()
            if response.status in REDIRECT:
                location = response.getheader("Location")
                if not location:
                    raise BridgeError("network_error", "The media server returned an incomplete redirect.")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise http_error(response.status, {k.lower(): v for k, v in response.getheaders()}, "media")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise BridgeError("media_unavailable", "The media response used an unsupported content encoding.")
            yield response, url
            return
        except (OSError, HTTPException, UnicodeError):
            raise BridgeError("network_error", "The HTTPS media transfer was interrupted.") from None
        finally:
            con.close()
    raise BridgeError("unsafe_media_url", "The media response exceeded the redirect limit.")


def http_error(status: int, headers: dict, service: str, reason: str = "") -> BridgeError:
    code = ("rate_limited" if status == 429 or reason in {"quotaExceeded", "dailyLimitExceeded",
             "rateLimitExceeded", "userRateLimitExceeded", "liveChatRateLimitExceeded"} else
            "invalid_credentials" if status == 401 or reason in {"keyInvalid", "API_KEY_INVALID"} else
            "unauthorized" if status == 403 else "media_unavailable" if status in {404, 410, 451} else
            "network_error" if 500 <= status <= 599 else "request_rejected")
    retry = headers.get("retry-after", "")
    reset = headers.get("ratelimit-reset", "")
    after = int(retry) if retry.isascii() and retry.isdigit() and len(retry) < 10 else (
        max(0, int(reset) - int(time())) if reset.isascii() and reset.isdigit() and len(reset) < 12 else None)
    return BridgeError(code, f"{service} returned HTTP {status}.", http_status=status, retry_after_seconds=after)


def api_request(host, path, params, headers, *, method="GET", accepted=(200,), form=None) -> dict:
    """Fixed official API origins only. No redirects and no remote error strings."""
    if host not in {"www.googleapis.com", "api.twitch.tv", "id.twitch.tv"}:
        raise ValueError("Unexpected API origin")
    con = HTTPSConnection(host, timeout=12)
    try:
        request = path + ("?" + urlencode(params, doseq=True) if params else "")
        body = urlencode(form).encode("ascii") if form is not None else None
        form_headers = {"Content-Type": "application/x-www-form-urlencoded"} if form is not None else {}
        con.request(method, request, body=body, headers={"Accept": "application/json", **form_headers, **headers})
        res = con.getresponse()
        body = res.read(2_000_001)
        if len(body) > 2_000_000:
            raise BridgeError("invalid_response", "The platform response exceeded the configured size limit.")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = None
        if res.status not in accepted:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            details = error.get("errors") or [] if isinstance(error, dict) else []
            reason = details[0].get("reason", "") if details and isinstance(details[0], dict) else ""
            raise http_error(res.status, {k.lower(): v for k, v in res.getheaders()}, host, reason)
        if not isinstance(payload, dict):
            raise BridgeError("invalid_response", "The platform returned invalid metadata.")
        return payload
    except (HTTPException, OSError, UnicodeError):
        raise BridgeError("network_error", "The official platform API could not be reached.") from None
    finally:
        con.close()
