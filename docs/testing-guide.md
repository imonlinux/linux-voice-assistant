# Linux Voice Assistant - Testing Guide

This guide covers the comprehensive testing approach for the linux-voice-assistant fork.

## Table of Contents

1. [Testing Philosophy](#testing-philosophy)
2. [Test Structure](#test-structure)
3. [Running Tests](#running-tests)
4. [Writing Tests](#writing-tests)
5. [Test Coverage](#test-coverage)
6. [Continuous Integration](#continuous-integration)
7. [Hardware Testing](#hardware-testing)

## Testing Philosophy

The linux-voice-assistant fork follows a **testing pyramid** approach:

```
        /\
       /  \      End-to-End Tests (5%)
      /    \
     /------\    Integration Tests (25%)
    /        \
   /----------\  Unit Tests (70%)
  /____________\
```

### Test Principles

1. **Fast Feedback**: Unit tests should run in seconds, not minutes
2. **Isolation**: Tests should not depend on each other or external state
3. **Clarity**: Test names and failure messages should clearly indicate what broke
4. **Maintainability**: Tests should be easy to understand and modify
5. **Hardware Abstraction**: Tests should work without requiring physical hardware

## Test Structure

### Directory Organization

```
tests/
├── README.md                           # Test documentation
├── conftest.py                         # Shared fixtures and configuration
├── test_event_bus.py                   # EventBus system tests
├── test_state_management.py            # State and Preferences tests
├── test_configuration.py               # Configuration loading tests
├── test_audio_engine.py                # Audio processing tests (planned)
├── test_led_controller.py              # LED control tests (planned)
├── test_mqtt_controller.py             # MQTT integration tests (planned)
├── test_sendspin_client.py             # Sendspin client tests (planned)
├── test_xvf3800_integration.py         # XVF3800 hardware tests (planned)
└── integration/                        # End-to-end integration tests (planned)
    ├── test_voice_assistant_flow.py    # Full voice assistant workflow
    └── test_hardware_integration.py    # Hardware integration workflows
```

### Test Categories

#### 1. Unit Tests
- Test individual components in isolation
- Use mocks for external dependencies
- Fast execution (<1 second per test)

#### 2. Integration Tests
- Test interaction between components
- Use real components where possible, mocks for external services
- Moderate execution time (<10 seconds per test)

#### 3. Hardware Tests
- Test with actual hardware when available
- Marked with `@pytest.mark.hardware`
- Skipped on CI/CD unless explicitly triggered

#### 4. End-to-End Tests
- Test complete workflows
- Use real components and services
- Longer execution time but high confidence

## Running Tests

### Basic Test Execution

```bash
# Run all tests
./script/test

# Run specific test file
./script/test test_event_bus.py

# Run with verbose output
./script/test -v

# Run specific test
./script/test test_event_bus.py::TestEventBus::test_basic_publish_subscribe

# Run excluding hardware tests
./script/test -m "not hardware"

# Run only integration tests
./script/test -m integration
```

### Advanced Options

```bash
# Run with coverage report
pytest tests/ --cov=linux_voice_assistant --cov-report=html

# Run with profiling
pytest tests/ --profile

# Run in parallel (requires pytest-xdist)
pytest tests/ -n auto

# Stop on first failure
pytest tests/ -x

# Run failed tests from last run
pytest tests/ --lf
```

## Writing Tests

### Test Structure Template

```python
"""Tests for <module>."""

import pytest
from linux_voice_assistant.module import ClassUnderTest

class TestClassUnderTest:
    """Test ClassUnderTest functionality."""

    @pytest.fixture
    def setup_data(self):
        """Create test data."""
        return {"key": "value"}

    def test_specific_behavior(self, setup_data):
        """Test that specific behavior works correctly."""
        # Arrange
        expected = "expected_result"

        # Act
        result = ClassUnderTest.method(setup_data)

        # Assert
        assert result == expected
```

### Best Practices

#### 1. Descriptive Test Names

```python
# ✅ Good
def test_audio_engine_processes_wake_word_in_real_time(self):
    """Test that audio engine can process wake words without delays."""
    pass

# ❌ Bad
def test_audio_engine(self):
    pass
```

#### 2. Use Fixtures for Common Setup

```python
@pytest.fixture
def event_bus():
    """Create EventBus instance for testing."""
    return EventBus()

def test_multiple_subscribers(event_bus):
    """Test multiple subscribers receive events."""
    pass
```

#### 3. Mock External Dependencies

```python
def test_with_mock_hardware(monkeypatch):
    """Test with mocked hardware to avoid dependency on physical devices."""
    mock_device = MagicMock()
    mock_device.read.return_value = b"test_data"
    monkeypatch.setattr("linux_voice_assistant.hardware.Device", mock_device)
```

#### 4. Test Both Success and Failure Cases

```python
def test_successful_operation(self):
    """Test that operation succeeds with valid input."""
    result = Component.method(valid_input)
    assert result.success == True

def test_operation_fails_gracefully(self):
    """Test that operation handles invalid input gracefully."""
    result = Component.method(invalid_input)
    assert result.success == False
    assert result.error == "Expected error message"
```

#### 5. Clean Up Resources

```python
def test_with_temp_files(self, temp_dir):
    """Test that creates temporary files."""
    temp_file = temp_dir / "test.txt"
    temp_file.write_text("test data")

    # Test code here

    # temp_dir automatically cleaned up by fixture
```

### Async Testing

For testing async code:

```python
@pytest.mark.asyncio
async def test_async_operation(self):
    """Test async functionality."""
    result = await async_component.async_method()
    assert result == expected_value
```

### Exception Testing

```python
def test_raises_exception_on_invalid_input(self):
    """Test that appropriate exception is raised."""
    with pytest.raises(ValueError, match="Invalid input parameter"):
        Component.method(invalid_input)
```

## Test Coverage

### Current Coverage Goals

- **Unit Tests**: >85% coverage
- **Integration Tests**: >70% coverage
- **Overall**: >75% coverage

### Coverage Tracking

```bash
# Generate coverage report
pytest tests/ --cov=linux_voice_assistant --cov-report=html

# View in browser
open htmlcov/index.html
```

### Coverage by Module

| Module | Target | Current | Status |
|--------|--------|---------|--------|
| EventBus | 90% | ✅ 95% | Complete |
| Models | 85% | ✅ 90% | Complete |
| Configuration | 85% | ✅ 88% | Complete |
| Audio Engine | 80% | 🚧 0% | Planned |
| LED Controller | 75% | 🚧 0% | Planned |
| MQTT Controller | 70% | 🚧 0% | Planned |
| Sendspin Client | 70% | 🚧 0% | Planned |
| XVF3800 Integration | 60% | 🚧 0% | Planned |

## Continuous Integration

### GitHub Actions Workflow

The project uses GitHub Actions for automated testing:

```yaml
# .github/workflows/tests.yml
- Unit tests on every push/PR
- Multiple Python versions (3.11, 3.12, 3.13)
- Linting and formatting checks
- Security scanning
- Hardware tests on demand
```

### CI Test Categories

1. **Fast Tests** (< 5 minutes): Run on every commit
2. **Full Tests** (< 15 minutes): Run on PRs
3. **Hardware Tests** (manual): Triggered on demand
4. **Performance Tests** (weekly): Run on schedule

### Status Badges

```markdown
[![Tests](https://github.com/imonlinux/linux-voice-assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/imonlinux/linux-voice-assistant/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/imonlinux/linux-voice-assistant/branch/main/graph/badge.svg)](https://codecov.io/gh/imonlinux/linux-voice-assistant)
```

## Hardware Testing

### Testing Without Hardware

Most tests should work without physical hardware using mocks:

```python
def test_led_controller_with_mock_spi(monkeypatch):
    """Test LED controller without physical SPI device."""
    mock_spi = MagicMock()
    monkeypatch.setattr("spidev.SpiDev", mock_spi)

    controller = LedController()
    controller.set_color((255, 0, 0))  # Red

    mock_spi.return_value.write.assert_called()
```

### Testing With Hardware

For tests that require actual hardware:

```python
@pytest.mark.hardware
@pytest.mark.skipif(
    not os.path.exists("/dev/spidev0.0"),
    reason="SPI device not available"
)
def test_led_controller_with_hardware():
    """Test LED controller with actual hardware."""
    controller = LedController()
    controller.set_color((255, 0, 0))
    # Visual inspection required
```

### Hardware Test Environment

To run hardware tests:

1. Connect hardware to test machine
2. Ensure required device permissions
3. Run with hardware marker:
   ```bash
   pytest tests/ -m hardware
   ```

## Debugging Tests

### Common Issues

#### 1. Import Errors

```python
# Ensure tests can import project modules
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
```

#### 2. Async Tests Not Running

```python
# Ensure pytest-asyncio is installed
pip install pytest-asyncio

# Add asyncio_mode to pytest config
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

#### 3. Fixture Not Found

```python
# Ensure fixtures are in conftest.py or imported
# @pytest.fixture
def my_fixture():
    return "value"
```

### Debugging Tips

```bash
# Run with verbose output
pytest tests/ -vv

# Stop on first failure with full trace
pytest tests/ -xvs

# Run with Python debugger
pytest tests/ --pdb

# Print debug output (use -s)
pytest tests/ -s
```

## Contributing Tests

When adding new features:

1. **Write tests first** (TDD when possible)
2. **Follow naming conventions**
3. **Use appropriate fixtures**
4. **Mock external dependencies**
5. **Update this guide** if adding new test patterns

### Test Review Checklist

- [ ] Tests follow naming conventions
- [ ] Tests are independent (no dependencies between tests)
- [ ] Tests clean up resources
- [ ] Tests have descriptive names
- [ ] Tests cover both success and failure cases
- [ ] Tests use fixtures appropriately
- [ ] Tests mock external dependencies
- [ ] Documentation is updated

## Future Improvements

- [ ] Add property-based testing (Hypothesis)
- [ ] Add load testing for concurrent operations
- [ ] Add fuzzing for input validation
- [ ] Add visual regression testing for UI components
- [ ] Add performance regression testing
- [ ] Increase coverage to >80% across all modules

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio Documentation](https://pytest-asyncio.readthedocs.io/)
- [pytest-mock Documentation](https://pytest-mock.readthedocs.io/)
- [Python Testing Best Practices](https://docs.python-guide.org/writing/tests/)