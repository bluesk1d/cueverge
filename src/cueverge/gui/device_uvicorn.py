import asyncio
import ssl

from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.protocols.websockets.websockets_sansio_impl import (
    WebSocketsSansIOProtocol as UvicornWebSocketProtocol,
)

from cueverge.gui.device_transport import PEER_CERTIFICATE_STATE_KEY


class PeerCertificateWebSocketProtocol(UvicornWebSocketProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        super().connection_made(transport)
        ssl_object = transport.get_extra_info("ssl_object")
        certificate_der = (
            ssl_object.getpeercert(binary_form=True)
            if isinstance(ssl_object, ssl.SSLObject)
            else None
        )
        if not certificate_der:
            transport.close()
            return
        self.app_state = self.app_state.copy()
        self.app_state[PEER_CERTIFICATE_STATE_KEY] = certificate_der


class PeerCertificateHttpProtocol(H11Protocol):
    """Expose the verified client certificate to HTTPS requests such as artifact downloads.

    Upgraded WebSocket connections inherit this state from the HTTP protocol.
    """

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        ssl_object = transport.get_extra_info("ssl_object")
        certificate_der = (
            ssl_object.getpeercert(binary_form=True)
            if isinstance(ssl_object, ssl.SSLObject)
            else None
        )
        if not certificate_der:
            transport.close()
            return
        self.app_state = self.app_state.copy()
        self.app_state[PEER_CERTIFICATE_STATE_KEY] = certificate_der
