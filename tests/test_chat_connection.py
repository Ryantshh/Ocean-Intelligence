import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ai_platform.app.chat_connection import install_connection_recovery


class ConnectionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connect = AsyncMock(return_value=True)
        self.message = AsyncMock()
        self.sio = SimpleNamespace(
            handlers={"/": {"connect": self.connect, "client_message": self.message}},
            save_session=AsyncMock(),
            get_session=AsyncMock(return_value={"oi_auth": {"sessionId": "session", "chatProfile": "agent"}}),
            get_environ=Mock(return_value={"HTTP_COOKIE": "existing-auth"}),
            emit=AsyncMock(),
        )
        self.sio.on = lambda name, fn: self.sio.handlers["/"].update({name: fn})
        self.backend = SimpleNamespace(
            sio=self.sio, WebsocketSession=SimpleNamespace(get=Mock(return_value=None)),
            connection_successful=AsyncMock(),
        )
        install_connection_recovery(self.backend)

    async def test_handover_reauthenticates_and_delivers_once(self):
        payload = {"message": {"id": "original", "content": "cargoes"}}
        await self.sio.handlers["/"]["client_message"]("old-socket", payload)
        self.connect.assert_awaited_once_with("old-socket", {"HTTP_COOKIE": "existing-auth"},
                                              {"sessionId": "session", "chatProfile": "agent"})
        self.backend.connection_successful.assert_awaited_once_with("old-socket")
        self.message.assert_awaited_once_with("old-socket", payload)

    async def test_healthy_connection_is_not_reconnected(self):
        self.backend.WebsocketSession.get.return_value = object()
        await self.sio.handlers["/"]["client_message"]("healthy", {})
        self.connect.assert_not_awaited()
        self.message.assert_awaited_once_with("healthy", {})

    async def test_rejected_authentication_does_not_deliver(self):
        self.connect.return_value = False
        await self.sio.handlers["/"]["client_message"]("stale", {})
        self.message.assert_not_awaited()

    async def test_unknown_socket_cannot_restore_a_session(self):
        self.sio.get_session.return_value = {}
        await self.sio.handlers["/"]["client_message"]("unknown", {})
        self.connect.assert_not_awaited()
        self.message.assert_not_awaited()
        self.sio.emit.assert_awaited_once()

    async def test_only_authenticated_socket_remembers_auth(self):
        self.connect.return_value = False
        await self.sio.handlers["/"]["connect"]("rejected", {}, {"sessionId": "other"})
        self.sio.save_session.assert_not_awaited()

    def test_install_is_idempotent(self):
        handler = self.sio.handlers["/"]["client_message"]
        install_connection_recovery(self.backend)
        self.assertIs(handler, self.sio.handlers["/"]["client_message"])
