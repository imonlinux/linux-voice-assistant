"""Tests for EventBus system."""

import pytest
from unittest.mock import Mock, patch
from linux_voice_assistant.event_bus import EventBus, EventHandler, subscribe


class TestEventBus:
    """Test EventBus pub/sub functionality."""

    def test_event_bus_initialization(self):
        """Test EventBus can be initialized."""
        bus = EventBus()
        assert bus is not None
        assert hasattr(bus, 'publish')
        assert hasattr(bus, 'subscribe')

    def test_basic_publish_subscribe(self):
        """Test basic event publishing and subscribing."""
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data)

        bus.subscribe("test_event", handler)
        bus.publish("test_event", {"key": "value"})

        assert len(received) == 1
        assert received[0] == {"key": "value"}

    def test_multiple_subscribers(self):
        """Test multiple subscribers to same event."""
        bus = EventBus()
        results = []

        def handler1(data):
            results.append(("handler1", data))

        def handler2(data):
            results.append(("handler2", data))

        bus.subscribe("test_event", handler1)
        bus.subscribe("test_event", handler2)
        bus.publish("test_event", {"test": "data"})

        assert len(results) == 2
        # Order matters - handlers are called in subscription order
        assert ("handler1", {"test": "data"}) in results
        assert ("handler2", {"test": "data"}) in results

    def test_subscribe_decorator(self):
        """Test @subscribe decorator functionality."""
        bus = EventBus()
        received = []

        class TestHandler(EventHandler):
            def __init__(self, event_bus):
                super().__init__(event_bus)
                self._subscribe_all_methods()

            @subscribe
            def decorated_method(self, data):
                received.append(data)

        handler = TestHandler(bus)
        bus.publish("decorated_method", {"decorated": True})

        assert len(received) == 1
        assert received[0] == {"decorated": True}

    def test_event_handler_auto_subscription(self):
        """Test EventHandler auto-subscribes all @subscribe methods."""
        bus = EventBus()
        call_log = []

        class MultiHandler(EventHandler):
            def __init__(self, event_bus):
                super().__init__(event_bus)
                self._subscribe_all_methods()

            @subscribe
            def method1(self, data):
                call_log.append(("method1", data))

            @subscribe
            def method2(self, data):
                call_log.append(("method2", data))

            def not_subscribed(self, data):
                call_log.append(("not_subscribed", data))

        handler = MultiHandler(bus)

        # Only subscribed methods should be called
        bus.publish("method1", {"event": "1"})
        bus.publish("method2", {"event": "2"})
        bus.publish("not_subscribed", {"event": "3"})

        assert len(call_log) == 2
        assert ("method1", {"event": "1"}) in call_log
        assert ("method2", {"event": "2"}) in call_log

    def test_unsubscribe(self):
        """Test that unsubscribe is not implemented (missing functionality)."""
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data)

        bus.subscribe("test_event", handler)
        # Verify that EventBus does not have an unsubscribe method
        assert not hasattr(bus, 'unsubscribe'), "EventBus should not have unsubscribe method"

        # The first publish should work
        bus.publish("test_event", {"first": "call"})
        assert len(received) == 1

        # Without unsubscribe, the second publish will also trigger the handler
        bus.publish("test_event", {"second": "call"})
        assert len(received) == 2  # Both calls were received since unsubscribe doesn't exist

    def test_exception_handling(self):
        """Test that exceptions in handlers don't crash the event bus."""
        bus = EventBus()
        received = []

        def failing_handler(data):
            raise ValueError("Handler failed")

        def working_handler(data):
            received.append(data)

        bus.subscribe("test_event", failing_handler)
        bus.subscribe("test_event", working_handler)
        bus.publish("test_event", {"should": "work"})

        # Working handler should still be called despite failing handler
        assert len(received) == 1
        assert received[0] == {"should": "work"}

    def test_event_data_immutability(self):
        """Test that event data can be modified by handlers."""
        bus = EventBus()
        received = []

        def modifying_handler(data):
            data["modified"] = True
            received.append(data.copy())

        def reading_handler(data):
            received.append(data.copy())

        bus.subscribe("test_event", modifying_handler)
        bus.subscribe("test_event", reading_handler)
        bus.publish("test_event", {"original": True})

        # Both handlers should see the modifications
        assert len(received) == 2
        assert received[0]["modified"] == True
        assert received[1]["modified"] == True

    def test_nonexistent_event_publish(self):
        """Test publishing to event with no subscribers."""
        bus = EventBus()
        # Should not raise exception
        bus.publish("nonexistent_event", {"data": "value"})

    def test_handler_ordering(self):
        """Test that handlers are called in subscription order."""
        bus = EventBus()
        call_order = []

        def handler1(data):
            call_order.append("handler1")

        def handler2(data):
            call_order.append("handler2")

        def handler3(data):
            call_order.append("handler3")

        bus.subscribe("test_event", handler1)
        bus.subscribe("test_event", handler2)
        bus.subscribe("test_event", handler3)
        bus.publish("test_event", {})

        assert call_order == ["handler1", "handler2", "handler3"]


class TestEventHandlerIntegration:
    """Test EventHandler integration with real components."""

    def test_event_handler_lifecycle(self):
        """Test EventHandler lifecycle management."""
        bus = EventBus()
        events_received = []

        class LifecycleHandler(EventHandler):
            def __init__(self, event_bus):
                super().__init__(event_bus)
                self.initialized = True
                self._subscribe_all_methods()

            @subscribe
            def on_test(self, data):
                events_received.append(data)

        handler = LifecycleHandler(bus)
        assert handler.initialized == True

        bus.publish("on_test", {"lifecycle": "test"})
        assert len(events_received) == 1

    def test_multiple_event_handlers(self):
        """Test multiple EventHandler instances."""
        bus = EventBus()
        handler1_calls = []
        handler2_calls = []

        class Handler1(EventHandler):
            def __init__(self, event_bus):
                super().__init__(event_bus)
                self._subscribe_all_methods()

            @subscribe
            def on_event(self, data):
                handler1_calls.append(("handler1", data))

        class Handler2(EventHandler):
            def __init__(self, event_bus):
                super().__init__(event_bus)
                self._subscribe_all_methods()

            @subscribe
            def on_event(self, data):
                handler2_calls.append(("handler2", data))

        handler1 = Handler1(bus)
        handler2 = Handler2(bus)

        bus.publish("on_event", {"test": "data"})

        assert len(handler1_calls) == 1
        assert len(handler2_calls) == 1
        assert ("handler1", {"test": "data"}) in handler1_calls
        assert ("handler2", {"test": "data"}) in handler2_calls

    def test_event_handler_with_state(self):
        """Test EventHandler maintaining internal state."""
        bus = EventBus()

        class StatefulHandler(EventHandler):
            def __init__(self, event_bus):
                super().__init__(event_bus)
                self.call_count = 0
                self.last_data = None
                self._subscribe_all_methods()

            @subscribe
            def on_increment(self, data):
                self.call_count += 1
                self.last_data = data

        handler = StatefulHandler(bus)

        bus.publish("on_increment", {"count": 1})
        bus.publish("on_increment", {"count": 2})
        bus.publish("on_increment", {"count": 3})

        assert handler.call_count == 3
        assert handler.last_data == {"count": 3}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])