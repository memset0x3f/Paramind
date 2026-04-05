#!/usr/bin/env python3
"""Smoke-test the current ParaMind desktop backend API."""

import requests
import json
import time

BASE_URL = "http://127.0.0.1:5001"


def test_endpoint(method, endpoint, data=None, expected_status=200):
    """Test a single API endpoint"""
    url = f"{BASE_URL}{endpoint}"

    try:
        if method.upper() == "GET":
            response = requests.get(url)
        elif method.upper() == "POST":
            response = requests.post(url, json=data)
        else:
            print(f"❌ Unsupported method: {method}")
            return False

        if response.status_code == expected_status:
            print(f"✅ {method} {endpoint} - Status: {response.status_code}")
            try:
                result = response.json()
                print(
                    f"   Response: {json.dumps(result, indent=2, ensure_ascii=False)}"
                )
            except:
                print(f"   Response: {response.text}")
            return True
        else:
            print(
                f"❌ {method} {endpoint} - Expected: {expected_status}, Got: {response.status_code}"
            )
            print(f"   Response: {response.text}")
            return False

    except requests.exceptions.ConnectionError:
        print(f"❌ {method} {endpoint} - Connection failed (server not running?)")
        return False
    except Exception as e:
        print(f"❌ {method} {endpoint} - Error: {e}")
        return False


def main():
    print("🧪 Testing ParaMind FastAPI Desktop Backend")
    print("=" * 50)

    # Wait a moment for server to be ready
    time.sleep(1)

    bootstrap_ok = test_endpoint("GET", "/api/bootstrap", None, 200)
    if not bootstrap_ok:
        print("⚠️ bootstrap failed; skipping the rest")
        return False

    bootstrap = requests.get(f"{BASE_URL}/api/bootstrap").json()
    conversation_id = bootstrap["conversations"][0]["id"]

    tests = [
        ("GET", "/api/health", None, 200),
        ("GET", "/api/network/status", None, 200),
        ("GET", "/api/conversations", None, 200),
        (
            "POST",
            f"/api/conversations/{conversation_id}/messages",
            {"role": "user", "content": "Hello from smoke test"},
            201,
        ),
    ]

    passed = 0
    total = len(tests)

    for method, endpoint, data, expected_status in tests:
        if test_endpoint(method, endpoint, data, expected_status):
            passed += 1
        print()  # Empty line for readability

    print("=" * 50)
    print(f"📊 Test Results: {passed}/{total} tests passed")

    if passed == total:
        print("🎉 All tests passed! Backend is working correctly.")
        return True
    else:
        print("⚠️  Some tests failed. Check the output above.")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
