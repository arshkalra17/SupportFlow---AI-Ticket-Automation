#!/usr/bin/env python3
"""
Live test script for Stage 12 Fix: Context-Aware Classification

Tests the conversation flow:
1. Where is order 1008?
2. Can I cancel it?
3. Actually, check order 1011.
4. What's the status?

Verifies that the final request uses order 1011 from context.

Usage:
    PYTHONPATH=. python scripts/manual/context_flow_demo.py
    
Requires:
    - Backend running (uvicorn app.main:app --reload)
    - GROQ_API_KEY environment variable set
    - Test customer account registered
"""

import os
import requests
import json
from typing import Dict, Any


# Configuration
API_BASE = "http://localhost:8000"
TEST_EMAIL = "context_test@example.com"
TEST_PASSWORD = "test_password_123"


def register_customer() -> Dict[str, Any]:
    """Register test customer (or skip if exists)."""
    response = requests.post(
        f"{API_BASE}/auth/register",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    if response.status_code in (200, 201):
        print(f"✅ Customer registered: {TEST_EMAIL}")
        return response.json()
    elif response.status_code == 400 and "already registered" in response.text.lower():
        print(f"ℹ️  Customer already exists: {TEST_EMAIL}")
        return {"message": "Already exists"}
    else:
        print(f"❌ Registration failed: {response.status_code} - {response.text}")
        return {}


def login_customer() -> str:
    """Login and get JWT token."""
    response = requests.post(
        f"{API_BASE}/auth/login",
        json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    if response.status_code == 200:
        token = response.json()["access_token"]
        print(f"✅ Logged in successfully")
        return token
    else:
        print(f"❌ Login failed: {response.status_code} - {response.text}")
        return ""


def send_message(token: str, message: str, turn: int) -> Dict[str, Any]:
    """Send a message to the support API."""
    print(f"\n{'='*70}")
    print(f"TURN {turn}: {message}")
    print(f"{'='*70}")
    
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.post(
        f"{API_BASE}/support/process",
        headers=headers,
        json={"message": message}
    )
    
    if response.status_code == 200:
        data = response.json()
        
        # Print classification
        if data.get("classification"):
            cls = data["classification"]
            print(f"\n📊 Classification:")
            print(f"   Category:  {cls.get('category')}")
            print(f"   Priority:  {cls.get('priority')}")
            print(f"   Sentiment: {cls.get('sentiment')}")
        
        # Print tool calls
        if data.get("tool_calls"):
            print(f"\n🔧 Tool Calls:")
            for i, tc in enumerate(data["tool_calls"], 1):
                print(f"   {i}. {tc['tool_name']}({tc['tool_args']})")
                print(f"      Validation:    {'✅' if tc['validation_passed'] else '❌'}")
                print(f"      Authorization: {'✅' if tc.get('authorization_passed') else '❌'}")
                print(f"      Executed:      {'✅' if tc['backend_executed'] else '❌'}")
        
        # Print final response
        print(f"\n💬 Response:")
        print(f"   {data.get('final_response', 'No response')}")
        
        return data
    else:
        print(f"❌ Request failed: {response.status_code} - {response.text}")
        return {}


def main():
    """Run the test conversation flow."""
    print("🚀 Starting Stage 12 Fix Live Test\n")
    
    # Check environment
    if not os.getenv("GROQ_API_KEY"):
        print("❌ ERROR: GROQ_API_KEY environment variable not set")
        return
    
    # Setup
    print("📝 Setup Phase")
    print("-" * 70)
    register_customer()
    token = login_customer()
    
    if not token:
        print("❌ Cannot proceed without authentication")
        return
    
    # Test conversation flow
    print("\n\n🎭 Test Conversation Flow")
    print("-" * 70)
    
    results = []
    
    # Turn 1: Explicit order reference
    result1 = send_message(token, "Where is order 1008?", 1)
    results.append(result1)
    
    # Turn 2: Implicit reference to order 1008
    result2 = send_message(token, "Can I cancel it?", 2)
    results.append(result2)
    
    # Turn 3: Switch context to order 1011
    result3 = send_message(token, "Actually, check order 1011.", 3)
    results.append(result3)
    
    # Turn 4: Implicit reference (should use order 1011)
    result4 = send_message(token, "What's the status?", 4)
    results.append(result4)
    
    # Verification
    print("\n\n" + "=" * 70)
    print("📋 VERIFICATION RESULTS")
    print("=" * 70)
    
    # Check Turn 4 used order 1011
    turn4_tool_calls = result4.get("tool_calls", [])
    if turn4_tool_calls:
        last_tool = turn4_tool_calls[-1]
        order_id_used = last_tool.get("tool_args", {}).get("order_id")
        
        if order_id_used == 1011:
            print("✅ PASS: Turn 4 correctly used order 1011 from context")
        else:
            print(f"❌ FAIL: Turn 4 used order {order_id_used}, expected 1011")
    else:
        print("❌ FAIL: Turn 4 did not call any tools (misrouted to RAG?)")
        print(f"   Classification: {result4.get('classification', {}).get('category')}")
    
    # Check routing
    print(f"\n📊 Turn 4 Routing:")
    print(f"   Message: 'What's the status?'")
    print(f"   Classification: {result4.get('classification', {}).get('category')}")
    print(f"   Tool Calls: {len(turn4_tool_calls)}")
    if turn4_tool_calls:
        print(f"   Order ID Used: {turn4_tool_calls[-1].get('tool_args', {}).get('order_id')}")
    
    print("\n" + "=" * 70)
    print("✅ Test completed. Review results above.")
    print("=" * 70)


if __name__ == "__main__":
    main()
