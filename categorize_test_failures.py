#!/usr/bin/env python3
"""
Categorize test failures into:
1. Test bugs (wrong API usage, outdated mocks, incorrect expectations)
2. Code issues (actual bugs in implementation)
3. Environment issues (missing dependencies, platform-specific)
4. Test infrastructure issues (async setup, fixtures, etc.)
"""

import subprocess
import sys
import re

def run_pytest_and_capture_failures():
    """Run pytest and capture failure details."""
    result = subprocess.run(
        ["pytest", "-v", "--tb=no", "-q"],
        capture_output=True,
        text=True
    )
    return result.stdout + result.stderr

def categorize_failure(test_name, error_msg):
    """Categorize a specific test failure."""
    error_lower = error_msg.lower()

    # Test infrastructure issues
    if any(term in error_lower for term in [
        "coroutine.*was never awaited",
        "event loop",
        "asyncio",
        "fixture",
        "mock.*call"
    ]):
        return "Test Infrastructure"

    # Test bugs (API mismatches)
    if any(term in error_lower for term in [
        "attributeerror",
        "typeerror",
        "has no attribute",
        "missing.*required",
        "unexpected keyword",
        "assertionerror.*==",
        "failed"  # Generic assertion failures
    ]):
        return "Test Bug (API mismatch)"

    # Environment issues
    if any(term in error_lower for term in [
        "import",
        "module",
        "dependency",
        "not found",
        "no such file"
    ]):
        return "Environment Issue"

    # Code issues
    if any(term in error_lower for term in [
        "runtimeerror",
        "valueerror",
        "keyerror",
        "indexerror"
    ]):
        return "Code Issue"

    return "Unknown"

def main():
    print("Running pytest to capture failures...")
    print("=" * 60)

    output = run_pytest_and_capture_failures()

    # Parse test results
    failed_tests = []
    current_test = None

    for line in output.split('\n'):
        # Match test lines like "tests/test_file.py::TestClass::test_function FAILED"
        if '::' in line and 'FAILED' in line:
            test_name = line.split('FAILED')[0].strip()
            current_test = test_name
            failed_tests.append(test_name)
        # Match error lines
        elif current_test and ('Error' in line or 'error' in line.lower()):
            error_msg = line.strip()
            category = categorize_failure(current_test, error_msg)
            print(f"{category}: {current_test}")
            print(f"  → {error_msg}\n")
            current_test = None

    print("=" * 60)
    print(f"Total failed tests: {len(failed_tests)}")

if __name__ == "__main__":
    main()
