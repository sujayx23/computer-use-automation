#!/usr/bin/env python3
"""
Quick sanity check that GEMINI_API_KEY works and function calling behaves
as expected, before running the full discovery agent. Run this first.

Usage: python3 test_llm_connection.py
"""
import os
import sys

if not os.environ.get("GEMINI_API_KEY"):
    print("ERROR: GEMINI_API_KEY not set. export GEMINI_API_KEY=... first.")
    sys.exit(1)

from llm.gemini_client import GeminiClient
from llm.base import ToolSpec

tools = [
    ToolSpec(
        name="say_hello",
        description="Call this to say hello to someone by name.",
        parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    )
]

client = GeminiClient(
    model="gemini-flash-lite-latest",
    system_prompt="You are a test agent. When asked to greet someone, call the say_hello tool.",
    tools=tools,
)

turn = client.next_action("Please greet a user named Sujay using your tool.", None)
print("tool_name:", turn.tool_name)
print("tool_input:", turn.tool_input)
print("reasoning:", turn.reasoning_text)

if turn.tool_name == "say_hello" and turn.tool_input.get("name"):
    print("\nSUCCESS: Gemini function calling is working correctly.")
else:
    print("\nUNEXPECTED RESPONSE -- inspect output above before running full discovery.")
