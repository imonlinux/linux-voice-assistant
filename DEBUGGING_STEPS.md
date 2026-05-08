# Test Failure Debugging Steps

## Issue Description
After pulling latest commits, tests still showing same failures:
- **format_mac()** returning 'aa::b:b::cc::d:d:' instead of 'aa:bb:cc:dd:ee:ff'
- **Volume parsing** returning None instead of expected values
- Total: 242 passed, 49 failed (no improvement)

## Root Cause Analysis

### Volume Parsing (FIXED)
**Problem:** Tests mock `subprocess.run` to return bytes (`b"Volume: 50%\n"`), but the code uses `text=True` which normally converts stdout to string. Mocks bypass this conversion.

**Fix Applied:** Added bytes handling to both functions:
- `get_wpctl_sink_volume()` now checks `isinstance(out, bytes)` and decodes if needed
- `get_pulseaudio_sink_volume()` now checks `isinstance(out, bytes)` and decodes if needed

**Commit:** `8835192 - Fix volume parsing for mocked subprocess tests`

### format_mac (SUSPECTED CACHE ISSUE)
**Problem:** The committed code is correct, but test output suggests old code is running:
```python
# Committed code (CORRECT):
def format_mac(mac: str) -> str:
    clean_mac = mac.replace(":", "").replace("-", "").replace(".", "")
    return ":".join(clean_mac[i:i+2] for i in range(0, 12, 2))
```

**Expected behavior:** "aa:bb:cc:dd:ee:ff" → "aa:bb:cc:dd:ee:ff"
**Actual test output:** 'aa::b:b::cc::d:d:' (every other char with double colons)

This suggests Python bytecode cache (.pyc files) contains old buggy code.

## Debugging Steps

### Step 1: Pull Latest Changes
```bash
cd /home/pi/linux-voice-assistant
git fetch origin
git log origin/upstream_refactor --oneline -5  # Should show 8835192
git pull origin upstream_refactor
```

### Step 2: Clear Python Cache
```bash
# Clear all .pyc files and __pycache__ directories
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -type f -name "*.pyc" -delete
find . -type f -name "*.pyo" -delete

# Verify cache is cleared
find . -name "*.pyc" | head -5  # Should return nothing
```

### Step 3: Verify format_mac Function
```bash
# Run the debug script
python3 tests/test_format_mac.py
```

**Expected output:**
```
Testing format_mac function:
✓ format_mac('aa:bb:cc:dd:ee:ff') = 'aa:bb:cc:dd:ee:ff' (expected 'aa:bb:cc:dd:ee:ff')
✓ format_mac('aabbccddeeff') = 'aa:bb:cc:dd:ee:ff' (expected 'aa:bb:cc:dd:ee:ff')
✓ format_mac('aa-bb-cc-dd-ee-ff') = 'aa:bb:cc:dd:ee:ff' (expected 'aa:bb:cc:dd:ee:ff')
✓ format_mac('aabb.ccdd.eeff') = 'aa:bb:cc:dd:ee:ff' (expected 'aa:bb:cc:dd:ee:ff')
```

**If this fails:** The code isn't being imported correctly. Check:
```bash
python3 -c "import linux_voice_assistant.util; import inspect; print(inspect.getsource(linux_voice_assistant.util.format_mac))"
```

Should print the source code showing `range(0, 12, 2)` not `range(0, 12, 1)`.

### Step 4: Re-run Tests
```bash
# Clear cache again before running
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null

# Run tests with verbose output for the failing tests
pytest tests/test_state_management.py::TestMacAddressHandling::test_mac_address_format -v
pytest tests/test_volume_management.py::TestWpctlVolumeControl::test_get_wpctl_sink_volume_parsing -v
pytest tests/test_volume_management.py::TestPulseAudioVolumeControl::test_get_pulseaudio_sink_volume_parsing -v
```

## Expected Results After Fixes

### Should Pass (2 tests):
1. `test_mac_address_format` - format_mac should work correctly
2. `test_get_wpctl_sink_volume_parsing` - Volume parsing should handle bytes
3. `test_get_pulseaudio_sink_volume_parsing` - Volume parsing should handle bytes

### Should Still Fail (47 tests):
These are test bugs requiring test updates, not code issues:
- **ButtonController** (8 tests) - Using wrong parameter name `button_config` instead of `config`
- **LedConfig** (2 tests) - Using non-existent `spi_device` parameter
- **Configuration** (2 tests) - Expecting removed attributes `discovery_prefix`, `press_time_ms`
- **Preferences** (2 tests) - Expecting wrong default values (50 vs 1.0, None vs [])
- **Async tests** (11 tests) - pytest-asyncio not installed in test environment
- **Hardware mocks** (7 tests) - Complex mock expectations need updates
- **Other** (15 tests) - Various mock/hardware issues

See `TEST_FAILURE_ANALYSIS.md` for complete breakdown.

## If Issues Persist

### Check Python Version
```bash
python3 --version  # Should be 3.13.5
pytest --version    # Should be 9.0.3
```

### Check Import Path
```bash
python3 -c "import linux_voice_assistant.util; print(linux_voice_assistant.util.__file__)"
```
Should show: `/home/pi/linux-voice-assistant/linux_voice_assistant/util.py`

Not: `/usr/lib/python3.13/...` or anywhere else

### Manual Code Check
```bash
grep -A 8 "def format_mac" linux_voice_assistant/util.py
```
Should show:
```python
def format_mac(mac: str) -> str:
    """Format a hex MAC string with colons (e.g., aa:bb:cc:dd:ee:ff)."""
    # Remove existing colons and other separators
    clean_mac = mac.replace(":", "").replace("-", "").replace(".", "")

    # Format with colons every 2 characters
    return ":".join(clean_mac[i : i + 2] for i in range(0, 12, 2))
```

**Critical check:** The range must be `range(0, 12, 2)` with step=2, not `range(0, 12, 1)` or `range(0, 12)`.

## Next Actions

1. **Immediate:** Run debugging steps above to verify fixes
2. **If volume tests pass:** Commit confirmed working, move to test bug fixes
3. **If format_mac fails:** Need to investigate Python import/caching issues
4. **After code fixes confirmed:** Start fixing test bugs (ButtonController, LedConfig, etc.)

## Contact Information
If issues persist after clearing cache and pulling latest:
- Check git log shows commit `8835192`
- Verify util.py and audio_volume.py match committed versions
- Run test_format_mac.py to isolate the issue
