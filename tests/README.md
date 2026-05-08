# Linux Voice Assistant - Test Suite

This directory contains comprehensive tests for the linux-voice-assistant fork.

## Test Structure

```
tests/
├── README.md                           # This file
├── test_event_bus.py                   # EventBus pub/sub system tests
├── test_state_management.py            # State and Preferences tests
├── test_configuration.py               # Configuration loading and validation
├── test_microwakeword.py              # MicroWakeWord detection tests (existing)
├── test_openwakeword.py               # OpenWakeWord detection tests (existing)
├── lva_mic_capture.py                  # Audio capture utility (existing)
├── xvf3800_hid_mute_probe.py          # XVF3800 hardware probe (existing)
└── xvf3800_probe.py                    # XVF3800 device probe (existing)
```

## Running Tests

### Prerequisites

1. **Install dependencies**:
```bash
cd /path/to/linux-voice-assistant
./script/setup --dev
```

2. **Ensure virtual environment is activated**:
```bash
source .venv/bin/activate  # On Linux/Mac
# or
.venv\Scripts\activate     # On Windows
```

### Run All Tests

```bash
./script/test
```

### Run Specific Test Files

```bash
./script/test test_event_bus.py
./script/test test_state_management.py
./script/test test_configuration.py
```

### Run with Verbose Output

```bash
./script/test -v
```

### Run Specific Test

```bash
./script/test test_event_bus.py::TestEventBus::test_basic_publish_subscribe -v
```

## Test Coverage

### Phase 1: Core Architecture ✅
- **EventBus System** (`test_event_bus.py`)
  - Basic publish/subscribe
  - Multiple subscribers
  - Decorator functionality
  - Exception handling
  - Handler lifecycle

- **State Management** (`test_state_management.py`)
  - Preferences dataclass
  - ServerState initialization
  - MAC address handling
  - File persistence
  - State transitions

- **Configuration** (`test_configuration.py`)
  - Config loading and validation
  - Default values
  - Sound path resolution
  - MQTT/Button/Sendspin config

### Phase 2: Controllers (Pending)
- Audio Engine tests
- LED Controller tests
- Button Controller tests
- Volume Management tests

### Phase 3: Protocol & Communication (Pending)
- ESPHome protocol tests
- MQTT controller tests
- Sendspin client tests

### Phase 4: Hardware Integration (Pending)
- XVF3800 integration tests
- Audio subsystem tests

### Phase 5: End-to-End (Pending)
- Full voice assistant flow
- Hardware integration workflows

## Test Conventions

### Naming Conventions

- Test files: `test_<module_name>.py`
- Test classes: `Test<ClassName>`
- Test methods: `test_<specific_functionality>`

### Structure

```python
"""Tests for <module>."""

import pytest
from linux_voice_assistant.module import ClassUnderTest

class TestClassUnderTest:
    """Test ClassUnderTest functionality."""

    def test_specific_behavior(self):
        """Test that specific behavior works correctly."""
        # Arrange
        test_data = {"key": "value"}

        # Act
        result = ClassUnderTest.method(test_data)

        # Assert
        assert result == expected_value
```

### Fixtures

Use pytest fixtures for common test setup:

```python
@pytest.fixture
def event_loop(self):
    """Create event loop for async tests."""
    import asyncio
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def minimal_state(self, event_loop):
    """Create minimal ServerState for testing."""
    # Setup code
    return ServerState(...)
```

## Continuous Integration

The test suite is designed to run in CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    cd linux-voice-assistant
    ./script/setup --dev
    ./script/test
```

## Troubleshooting

### Import Errors

If you see import errors, ensure dependencies are installed:

```bash
./script/setup --dev
```

### Hardware Not Available Tests

Tests that require specific hardware (XVF3800, ReSpeaker HATs) should be marked as skipped if the hardware is not available:

```python
@pytest.mark.skipif(
    not os.path.exists("/dev/some_hardware_device"),
    reason="Hardware not available"
)
def test_hardware_integration(self):
    # Test code
    pass
```

### Audio Device Tests

Tests that require audio devices should mock the hardware or use virtual audio devices:

```python
@pytest.fixture
def mock_soundcard(monkeypatch):
    """Mock soundcard library for testing."""
    # Mock implementation
    pass
```

## Contributing Tests

When adding new functionality, follow these guidelines:

1. **Write tests first** (TDD approach when possible)
2. **Test both success and failure cases**
3. **Use descriptive test names**
4. **Keep tests focused** (one behavior per test)
5. **Mock external dependencies** (hardware, network, etc.)
6. **Clean up resources** (temp files, connections, etc.)

## Test Goals

- **Coverage**: Aim for >80% code coverage
- **Speed**: Tests should run in <30 seconds total
- **Reliability**: Tests should be deterministic and repeatable
- **Clarity**: Test failures should clearly indicate what broke

## Current Status

- ✅ Phase 1: Core Architecture (EventBus, State, Config)
- 🚧 Phase 2: Controllers (Audio, LED, Button, Volume)
- 📋 Phase 3: Protocol & Communication
- 📋 Phase 4: Hardware Integration
- 📋 Phase 5: End-to-End Workflows

## Future Improvements

- [ ] Add performance benchmarks
- [ ] Add fuzzing for input validation
- [ ] Add integration tests with actual hardware
- [ ] Add CI/CD integration
- [ ] Add code coverage reporting
- [ ] Add property-based testing (Hypothesis)
- [ ] Add load testing for concurrent operations