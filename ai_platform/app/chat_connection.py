"""Repair Chainlit socket handovers without dropping an already submitted message.

Profile selection can overlap two connections using one session ID. Chainlit's
restore() moves the session to the newest socket, but the browser can retain the
other socket. Its next client_message otherwise raises 'Session not found'.
"""
import logging
from functools import wraps

logger = logging.getLogger(__name__)


def install_connection_recovery(backend=None):
    if backend is None:
        import chainlit.socket as backend

    sio = backend.sio
    if getattr(sio, "_oi_connection_recovery", False):
        return
    original_connect = sio.handlers["/"]["connect"]
    original_message = sio.handlers["/"]["client_message"]

    @wraps(original_connect)
    async def connect(sid, environ, auth):
        accepted = await original_connect(sid, environ, auth)
        if accepted is not False:
            # Store only on a socket that passed Chainlit's authentication and
            # ownership checks. Socket.IO discards this on disconnect.
            await sio.save_session(sid, {"oi_auth": dict(auth)})
        return accepted

    @wraps(original_message)
    async def message(sid, payload):
        if backend.WebsocketSession.get(sid) is None:
            state = await sio.get_session(sid)
            auth = state.get("oi_auth")
            environ = sio.get_environ(sid)
            if not auth or environ is None:
                await sio.emit("connect_error", "Chat connection expired. Refresh to reconnect.", to=sid)
                return
            # Re-run the standard auth/ownership checks; never attach a session
            # based on a client-provided ID alone.
            accepted = await original_connect(sid, environ, auth)
            if accepted is False:
                return
            logger.warning("Recovered chat socket handover before processing message")
            await backend.connection_successful(sid)
        await original_message(sid, payload)

    sio.on("connect", connect)
    sio.on("client_message", message)
    sio._oi_connection_recovery = True
