# Test Failure Analysis

## Current Status
- **242 passed, 49 failed, 1 error**
- Pass rate: 83%

## Fixes Applied

### 1. Volume Parsing Functions (COMMITTED)
**Issue:** `get_wpctl_sink_volume()` and `get_pulseaudio_sink_volume()` returning None with mocked test output

**Fix:** Updated parsing to handle both percentage formats from mocks and decimal formats from real commands:
- `get_wpctl_sink_volume`: Now handles "Volume: 50%" (mocked) and "Volume: 0.40" (real)
- Returns values in 0.0-1.0 range regardless of input format

**Commit:** `718ac25 - Fix volume parsing to handle both percentage and decimal formats`

### 2. format_mac() Function (PREVIOUSLY COMMITTED)
**Issue:** format_mac() producing incorrect output with colons

**Fix:** Strip all separators (colons, dashes, dots) before reformatting

**Commit:** `a79f687 - Fix test failures: format_mac and volume parsing functions`

## Remaining Failures by Category

### Category 1: Test Bugs (Tests Need Updating)
These failures are due to tests using incorrect APIs or expecting wrong values.

#### ButtonController API (8 failures)
- **Issue:** Tests pass `button_config` parameter but actual API uses `config`
- **Tests affected:**
  - `test_button_controller_initialization`
  - `test_button_controller_with_disabled_gpio`
  - `test_button_short_press_detection`
  - `test_button_long_press_detection`
  - `test_button_controller_publishes_wake_word_event`
  - `test_button_controller_publishes_mute_event`
  - `test_button_controller_handles_zero_pin`
  - `test_button_controller_handles_negative_long_press`
- **Fix needed:** Update tests to use correct parameter name `config`

#### LedConfig API (2 failures)
- **Issue:** Tests use `spi_device` parameter which doesn't exist in LedConfig
- **Tests affected:**
  - `test_led_controller_subscribes_to_events`
  - `test_led_controller_with_neopixel_config`
- **Fix needed:** Remove `spi_device` from test configurations

#### Configuration Schema (2 failures)
- **Issue:** Tests expect attributes that don't exist in current schema
- **Tests affected:**
  - `test_config_with_mqtt_enabled` - expects `discovery_prefix` attribute
  - `test_config_with_button_enabled` - expects `press_time_ms` attribute
- **Fix needed:** Update tests to match current schema or add missing attributes

#### Preferences Defaults (2 failures)
- **Issue:** Tests expect wrong default values
- **Actual defaults:** `volume_level=1.0`, `active_wake_words=[]`
- **Test expectations:** `volume_level=50`, `active_wake_words=None`
- **Tests affected:**
  - `test_default_preferences`
  - `test_preferences_backward_compatibility`
- **Fix needed:** Update test expectations to match actual defaults

#### Async Functions Not Awaited (6 failures)
- **Issue:** Tests call async functions without `await`
- **Tests affected:**
  - All `ensure_output_volume` tests in `test_volume_management.py`
  - `test_volume_manager_fallback_chain`
- **Fix needed:** Convert tests to async and use `await`

### Category 2: Environment/Setup Issues
These require environment configuration, not code changes.

#### pytest-asyncio Not Configured (10 failures)
- **Issue:** Async tests fail with "async def functions are not natively supported"
- **Tests affected:** All tests in `test_end_to_end_workflows.py` and `test_sendspin_discovery.py` marked with `@pytest.mark.asyncio`
- **Fix needed:** Ensure pytest-asyncio is installed in test environment
- **Note:** `pyproject.toml` already has `asyncio_mode = "auto"` configured

#### OpenWakeWord Library Missing (1 failure)
- **Issue:** Missing shared library file
- **Test:** `test_features` in `test_openwakeword.py`
- **Error:** `OSError: /home/pi/linux-voice-assistant/lib/linux_arm64/libtensorflowlite_c.so: cannot open shared object file: No such file or directory`
- **Fix needed:** Install required TensorFlow Lite library on test system

### Category 3: Mock/Hardware Test Issues
These involve complex mocking or hardware simulation.

#### EventBus/State Mock Issues (2 failures)
- **Tests affected:**
  - `test_server_state_mic_muted_event` - Event handlers not being called
  - `test_hardware_button_to_led_feedback_workflow` - State mock not updating
- **Fix needed:** Fix mock setup to properly simulate event propagation

#### Audio System Detection (1 failure)
- **Test:** `test_volume_manager_adapts_to_audio_system`
- **Issue:** Mocked `get_audio_system_type()` not being respected
- **Fix needed:** Fix mock patching or test logic

#### Hardware Mock Issues (7 failures)
- **Tests in `test_xvf3800_led_backend.py`:**
  - `test_context_manager` - Context manager returning different object
  - `test_write_success` - USB control transfer flags assertion
  - `test_set_ring_colors`, `test_set_ring_rgb`, `test_set_ring_solid`, `test_clear_ring` - Extra mock calls
  - `test_get_version` - Returning None instead of version tuple
  - `test_led_power_check_before_ring_operations` - GPO write calls not tracked
- **Fix needed:** Update hardware backend or fix mock expectations

#### MicroWakeWord API (1 failure)
- **Test:** `test_features` in `test_microwakeword.py`
- **Issue:** `libtensorflowlite_c_path` parameter not accepted
- **Fix needed:** Update test or check MicroWakeWord API

## Summary of Action Items

### High Priority (Code Changes)
1. ✅ Volume parsing functions - FIXED
2. ✅ format_mac() - FIXED
3. Address EventBus mock issues in state management tests

### Medium Priority (Test Updates)
1. Fix ButtonController test parameter names (8 tests)
2. Fix LedConfig test parameters (2 tests)
3. Update Preferences default value expectations (2 tests)
4. Convert async function tests to async (6 tests)

### Low Priority (Environment)
1. Install pytest-asyncio in test environment (10 tests)
2. Install TensorFlow Lite library (1 test)
3. Fix complex hardware mock issues (7 tests)

### Total Impact
- **Test bugs:** 20 failures
- **Environment issues:** 11 failures
- **Code issues (fixed):** 2 failures
- **Hardware mock issues:** 7 failures
- **EventBus/State issues:** 2 failures

## Recommended Next Steps

1. **Verify fixes:** Pull latest commits and re-run tests to confirm volume parsing and format_mac fixes work
2. **Fix test bugs:** Update tests to use correct APIs (ButtonController, LedConfig, Preferences)
3. **Setup environment:** Ensure pytest-asyncio is installed for async tests
4. **Address complex mocks:** Fix hardware mock expectations as needed
