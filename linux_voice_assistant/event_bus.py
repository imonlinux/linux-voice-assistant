import logging
from typing import Any, Callable, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class EventBus:
    """A simple synchronous publish/subscribe event bus."""

    def __init__(self):
        # A dictionary to hold listeners for specific string topics
        self.topics: Dict[str, List[Callable[[Any], None]]] = {}

    def subscribe(self, topic: str, listener: Callable[[Any], None]) -> None:
        """
        Subscribes a listener to a topic.
        """
        if topic not in self.topics:
            self.topics[topic] = []
        self.topics[topic].append(listener)

    def publish(self, topic: str, data: Optional[Dict[str, Any]] = None) -> None:
        """
        Publishes an event to all subscribed listeners.
        """
        if data is None:
            data = {}

        data['__topic'] = topic

        listeners = self.topics.get(topic, [])
        for listener in listeners:
            try:
                listener(data)
            except Exception:
                _LOGGER.exception("Error in event listener for topic %s", topic)

# Client helpers for subscriptions

def subscribe(func: Callable) -> Callable:
    """Decorator to mark a method for event bus subscription."""
    func._event_bus_subscribe = True
    return func

class EventHandler:
    """
    A base class for components that subscribe to events.
    """
    def __init__(self, state: Any):
        self.state = state
        self._subscribe_all_methods()

    def _subscribe_all_methods(self):
        """Finds and subscribes all methods decorated with @subscribe."""
        for method_name in dir(self):
            method = getattr(self, method_name)
            
            if hasattr(method, '_event_bus_subscribe'):
                # The topic is the name of the method itself.
                self.state.event_bus.subscribe(method_name, method)
                _LOGGER.debug(f"Subscribed method '{method_name}' to topic '{method_name}'")
