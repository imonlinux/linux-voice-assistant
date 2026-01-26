"""A simple synchronous publish/subscribe event bus."""

import logging
from typing import Any, Callable, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class EventBus:
    """A simple synchronous publish/subscribe event bus."""

    def __init__(self):
        self.topics: Dict[str, List[Callable[[Any], None]]] = {}

    def subscribe(self, topic: str, listener: Callable[[Any], None]) -> None:
        """Subscribes a listener to a topic."""
        if topic not in self.topics:
            self.topics[topic] = []
        self.topics[topic].append(listener)
        _LOGGER.debug(f"Subscribed listener to topic '{topic}'")

    def publish(self, topic: str, data: Optional[Dict[str, Any]] = None) -> None:
        """
        Publishes an event to all subscribed listeners.

        IMPORTANT (Milestone 2 contract):
        - Do NOT mutate payloads (no injected keys like __topic).
        - Event payload shapes must remain stable and predictable.
        """
        if data is None:
            data = {}

        listeners = self.topics.get(topic, [])
        _LOGGER.debug(f"Publishing event to {len(listeners)} listeners on topic '{topic}'")
        for listener in listeners:
            try:
                listener(data)
            except Exception:
                _LOGGER.exception("Error in event listener for topic %s", topic)


# -----------------------------------------------------------------------------
# Client helpers for subscriptions
# -----------------------------------------------------------------------------

def subscribe(func: Callable) -> Callable:
    """Decorator to mark a method for event bus subscription."""
    func._event_bus_subscribe = True
    return func


class EventHandler:
    """
    A base class for components that subscribe to events.

    Subclasses must call `self._subscribe_all_methods()` in their __init__.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        # Note: Subclasses must call self._subscribe_all_methods()
        # after their own __init__ is complete.

    def _subscribe_all_methods(self):
        """Finds and subscribes all methods decorated with @subscribe."""
        for method_name in dir(self):
            method = getattr(self, method_name)

            if hasattr(method, '_event_bus_subscribe'):
                # The topic is the name of the method itself.
                self.event_bus.subscribe(method_name, method)
