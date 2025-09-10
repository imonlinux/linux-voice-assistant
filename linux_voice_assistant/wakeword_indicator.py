"""Wakeword Engine indicator helper.

Provides a simple function to update the text sensor state via APIServer.
"""
from .api_server import APIServer

SENSOR_OBJECT_ID = "wakeword_engine"

def set_wakeword_engine(server: APIServer, engine_name: str) -> None:
    """Publish the current wakeword engine name to Home Assistant.

    Call this after the engine is selected and whenever it changes.
    """
    msg = server.publish_text_sensor(SENSOR_OBJECT_ID, engine_name)
    if msg is not None:
        # Enqueue/send happens in the server run loop; callers should hand this to the transport.
        # For convenience, APIServer.handle_message() returns messages to write downstream.
        # Here we directly push by using APIServer._writelines if available.
        if getattr(server, "_writelines", None) is not None:
            server._writelines([msg])  # type: ignore[attr-defined]