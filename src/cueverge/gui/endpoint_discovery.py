import http.client
import ipaddress
import json
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse

from cueverge.protocol.device_v2 import ControllerDiscoveryDocument


class EndpointDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveredController:
    base_url: str
    document: ControllerDiscoveryDocument


class EndpointDiscoveryService:
    def __init__(self, *, timeout_seconds: float = 3.0) -> None:
        self.timeout_seconds = timeout_seconds

    def probe(self, value: str) -> DiscoveredController:
        base_url = self._validate_url(value)
        host = urlparse(base_url).hostname
        assert host is not None
        addresses = self._resolve(host)
        if not addresses or any(not _is_local(address) for address in addresses):
            raise EndpointDiscoveryError(
                "discovery targets must resolve only to private, loopback, or link-local addresses"
            )
        payload = self._fetch(
            f"{base_url}/api/device/v2/discovery",
            addresses,
        )
        try:
            document = ControllerDiscoveryDocument.model_validate_json(payload)
        except ValueError as error:
            raise EndpointDiscoveryError(
                "endpoint returned an invalid discovery document"
            ) from error
        return DiscoveredController(base_url=base_url, document=document)

    def _validate_url(self, value: str) -> str:
        parsed = urlparse(value.strip())
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise EndpointDiscoveryError(
                "discovery address must be an HTTPS origin without credentials, "
                "path, query, or fragment"
            )
        netloc = parsed.hostname
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse(("https", netloc, "", "", "", ""))

    def _resolve(self, host: str) -> tuple[str, ...]:
        try:
            return tuple(
                sorted(
                    {
                        str(result[4][0])
                        for result in socket.getaddrinfo(
                            host,
                            None,
                            type=socket.SOCK_STREAM,
                        )
                    }
                )
            )
        except socket.gaierror as error:
            raise EndpointDiscoveryError("discovery hostname could not be resolved") from error

    def _fetch(self, url: str, addresses: tuple[str, ...]) -> bytes:
        parsed = urlparse(url)
        host = parsed.hostname
        assert host is not None
        port = parsed.port or 443
        context = ssl.create_default_context()
        last_error: OSError | None = None
        for address in addresses:
            connection = http.client.HTTPSConnection(
                host,
                port,
                timeout=self.timeout_seconds,
                context=context,
            )
            try:
                raw_socket = socket.create_connection(
                    (address, port),
                    timeout=self.timeout_seconds,
                )
                connection.sock = context.wrap_socket(
                    raw_socket,
                    server_hostname=host,
                )
                connection.request(
                    "GET",
                    parsed.path,
                    headers={"Accept": "application/json"},
                )
                response = connection.getresponse()
                if response.status != 200:
                    raise EndpointDiscoveryError(
                        f"discovery endpoint returned HTTP {response.status}"
                    )
                payload = response.read(65_537)
                break
            except OSError as error:
                last_error = error
            finally:
                connection.close()
        else:
            raise EndpointDiscoveryError(f"discovery request failed: {last_error}") from last_error
        if len(payload) > 65_536:
            raise EndpointDiscoveryError("discovery response exceeds 64 KiB")
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as error:
            raise EndpointDiscoveryError("discovery response is not JSON") from error
        if not isinstance(value, dict):
            raise EndpointDiscoveryError("discovery response must be a JSON object")
        return payload


def _is_local(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return address.is_private or address.is_loopback or address.is_link_local
