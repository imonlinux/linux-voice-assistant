#!/usr/bin/env python3
"""
Direct test of format_mac and volume parsing functions.
Run this in your test environment to verify the code works correctly.
"""

import sys
import os

# Add the parent directory to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_format_mac():
    """Test the format_mac function directly."""
    print("=== Testing format_mac function ===\n")

    try:
        from linux_voice_assistant.util import format_mac

        # Test 1: MAC with colons (should return unchanged)
        mac_with_colons = "aa:bb:cc:dd:ee:ff"
        result1 = format_mac(mac_with_colons)
        expected1 = "aa:bb:cc:dd:ee:ff"
        status1 = "✓ PASS" if result1 == expected1 else "✗ FAIL"
        print(f"Test 1 - MAC with colons:")
        print(f"  Input:    '{mac_with_colons}'")
        print(f"  Expected: '{expected1}'")
        print(f"  Got:      '{result1}'")
        print(f"  {status1}\n")

        # Test 2: Raw MAC without colons (should add colons)
        raw_mac = "aabbccddeeff"
        result2 = format_mac(raw_mac)
        expected2 = "aa:bb:cc:dd:ee:ff"
        status2 = "✓ PASS" if result2 == expected2 else "✗ FAIL"
        print(f"Test 2 - Raw MAC without colons:")
        print(f"  Input:    '{raw_mac}'")
        print(f"  Expected: '{expected2}'")
        print(f"  Got:      '{result2}'")
        print(f"  {status2}\n")

        # Show the actual function code
        print("Actual function code:")
        import inspect
        print(inspect.getsource(format_mac))

        return result1 == expected1 and result2 == expected2

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_volume_parsing():
    """Test volume parsing function directly."""
    print("\n=== Testing volume parsing function ===\n")

    try:
        from linux_voice_assistant.audio_volume import get_wpctl_sink_volume
        import unittest.mock as mock

        # Test with mocked subprocess returning bytes (like tests do)
        print("Test 1 - Mocked subprocess returning bytes:")
        with mock.patch('linux_voice_assistant.audio_volume._run_cmd') as mock_run:
            # Mock subprocess returning bytes (as tests do)
            mock_run.return_value = (True, b'Volume: 50%')

            result = get_wpctl_sink_volume()
            expected = 50.0
            status = "✓ PASS" if result == expected else "✗ FAIL"
            print(f"  Mocked return: (True, b'Volume: 50%')")
            print(f"  Expected: {expected}")
            print(f"  Got:      {result}")
            print(f"  {status}\n")

        # Show the actual function code
        print("Actual function code:")
        import inspect
        print(inspect.getsource(get_wpctl_sink_volume))

        return result == expected

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all tests."""
    print("LVA Function Test Suite")
    print("=" * 50)
    print(f"Python version: {sys.version}")
    print(f"Working directory: {os.getcwd()}")
    print(f"Import path: {sys.path[0]}")
    print("=" * 50 + "\n")

    # Check which util.py file is being imported
    try:
        import linux_voice_assistant.util
        print(f"util.py location: {linux_voice_assistant.util.__file__}\n")
    except Exception as e:
        print(f"Could not import util: {e}\n")

    test1_pass = test_format_mac()
    test2_pass = test_volume_parsing()

    print("\n" + "=" * 50)
    print(f"Results: format_mac {'PASS' if test1_pass else 'FAIL'}, " +
          f"volume_parse {'PASS' if test2_pass else 'FAIL'}")
    print("=" * 50)

    return 0 if (test1_pass and test2_pass) else 1

if __name__ == "__main__":
    sys.exit(main())
