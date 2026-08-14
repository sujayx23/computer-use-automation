from __future__ import annotations

import time
from typing import Optional

from google import genai
from google.genai import types
from google.genai import errors as genai_errors

from llm.base import LLMClient, LLMTurn, ToolSpec

_TYPE_MAP = {
    "object": types.Type.OBJECT,
    "string": types.Type.STRING,
    "integer": types.Type.INTEGER,
    "number": types.Type.NUMBER,
    "boolean": types.Type.BOOLEAN,
    "array": types.Type.ARRAY,
}


def _to_gemini_schema(schema: dict) -> types.Schema:
    kwargs = {"type": _TYPE_MAP[schema["type"]]}
    if "properties" in schema:
        kwargs["properties"] = {k: _to_gemini_schema(v) for k, v in schema["properties"].items()}
    if "required" in schema:
        kwargs["required"] = schema["required"]
    if "items" in schema:
        kwargs["items"] = _to_gemini_schema(schema["items"])
    if "enum" in schema:
        kwargs["enum"] = schema["enum"]
    return types.Schema(**kwargs)


class GeminiClient(LLMClient):
    def __init__(self, model: str, system_prompt: str, tools: list[ToolSpec]):
        self.client = genai.Client()  # reads GEMINI_API_KEY from env
        self.model = model
        function_declarations = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters=_to_gemini_schema(t.parameters)
            )
            for t in tools
        ]
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=function_declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        self.contents: list[types.Content] = []
        self._last_tool_name: Optional[str] = None

    def next_action(self, observation_text: str, prior_tool_result: Optional[str]) -> LLMTurn:
        if prior_tool_result is not None and self._last_tool_name:
            self.contents.append(types.Content(
                role="user",
                parts=[types.Part.from_function_response(
                    name=self._last_tool_name, response={"result": prior_tool_result}
                )],
            ))
        self.contents.append(types.Content(role="user", parts=[types.Part(text=observation_text)]))

        resp = self._generate_with_retry()
        candidate = resp.candidates[0]
        self.contents.append(candidate.content)

        parts = candidate.content.parts or []
        fn_part = next((p for p in parts if getattr(p, "function_call", None)), None)
        reasoning = " ".join(p.text for p in parts if getattr(p, "text", None))

        if fn_part is None:
            return LLMTurn(tool_name=None, tool_input=None, reasoning_text=reasoning)

        self._last_tool_name = fn_part.function_call.name
        tool_input = dict(fn_part.function_call.args) if fn_part.function_call.args else {}
        return LLMTurn(tool_name=fn_part.function_call.name, tool_input=tool_input, reasoning_text=reasoning)

    def _generate_with_retry(self, max_attempts: int = 5, base_delay: float = 3.0):
        """Google's free-tier endpoints occasionally return 503 'high demand'
        errors or drop the connection outright under load -- both are
        transient by nature, so retry with exponential backoff rather than
        surfacing a hard failure for something that typically clears within
        seconds."""
        last_err = None
        for attempt in range(max_attempts):
            try:
                return self.client.models.generate_content(
                    model=self.model, contents=self.contents, config=self.config,
                )
            except Exception as e:
                # ClientError sometimes also fires transiently under load (429-style);
                # broad Exception here specifically catches transport-level failures
                # (connection reset, read timeout) that aren't genai-specific error types
                is_retryable = isinstance(e, (genai_errors.ServerError, genai_errors.ClientError)) or \
                    "Connection" in str(e) or "Read" in type(e).__name__ or "timeout" in str(e).lower()
                if not is_retryable or attempt == max_attempts - 1:
                    raise
                last_err = e
                delay = base_delay * (2 ** attempt)
                print(f"  [Gemini request failed ({type(e).__name__}), retrying in {delay:.0f}s "
                      f"-- attempt {attempt + 1}/{max_attempts}]")
                time.sleep(delay)
        raise last_err