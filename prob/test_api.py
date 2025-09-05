#!/usr/bin/env python3
"""
Test script to verify all API endpoints are working correctly
"""

import requests
import json
import time

BASE_URL = "http://localhost:5001"

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
                print(f"   Response: {json.dumps(result, indent=2, ensure_ascii=False)}")
            except:
                print(f"   Response: {response.text}")
            return True
        else:
            print(f"❌ {method} {endpoint} - Expected: {expected_status}, Got: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print(f"❌ {method} {endpoint} - Connection failed (server not running?)")
        return False
    except Exception as e:
        print(f"❌ {method} {endpoint} - Error: {e}")
        return False

def main():
    print("🧪 Testing Python Flask Backend API")
    print("=" * 50)
    
    # Wait a moment for server to be ready
    time.sleep(1)
    
    tests = [
        # Basic connectivity
        ("GET", "/", None, 200),
        ("GET", "/api/health", None, 200),
        
        # Message system
        ("POST", "/api/message", {"message": "Test message from Python", "from": "test-script"}, 200),
        ("GET", "/api/messages", None, 200),
        
        # Counter system
        ("GET", "/api/counter", None, 200),
        ("POST", "/api/counter", {"increment": 5}, 200),
        ("GET", "/api/counter", None, 200),
        
        # Calculator
        ("POST", "/api/calculate", {"operation": "add", "a": 20, "b": 30}, 200),
        ("POST", "/api/calculate", {"operation": "multiply", "a": 6, "b": 7}, 200),
        ("POST", "/api/calculate", {"operation": "divide", "a": 100, "b": 4}, 200),
        
        # User management
        ("POST", "/api/user", {"name": "Test User", "email": "test@example.com"}, 200),
        ("GET", "/api/users", None, 200),
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
