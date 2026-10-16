"""LLM provider adapter layer for RAiner.

This module provides a canonical interface for provider-specific message/tool
formats so the agent logic can remain model-agnostic.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

import anthropic as anthropic_sdk
import ollama
import openai as openai_sdk
from google import genai
from google.genai import types as google_types

from .config import get_config


@dataclass
class ToolCall:
    """Canonical tool call representation."""

    id: str
    name: str
    arguments: dict[str, Any]


ProviderName = Literal[
    "ollama",
    "ollama-cloud",
    "openai",
    "openrouter",
    "azure-openai",
    "anthropic",
    "google",
]


class ProviderAdapter(ABC):
    """Abstract adapter interface."""

    def __init__(self, model: str, temperature: float = 0.1) -> None:
        self.model = model
        self.temperature = temperature

    @abstractmethod
    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall] | None]:
        """Generate a response and optional tool calls."""

    @abstractmethod
    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result_content: str
    ) -> dict[str, Any]:
        """Format a tool result message for this provider."""


class OpenAIToolsMixin:
    """Converts tools to OpenAI-style JSON schema."""

    @staticmethod
    def get_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return tools


class OllamaAdapter(ProviderAdapter, OpenAIToolsMixin):
    def __init__(
        self,
        client: ollama.Client,
        model: str,
        temperature: float = 0.3,
        num_ctx: int | None = None,
    ) -> None:
        super().__init__(model=model, temperature=temperature)
        self.client = client
        self.num_ctx = num_ctx

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall] | None]:
        options: dict[str, Any] = {"temperature": self.temperature}
        if self.num_ctx:
            # Without this, Ollama uses its small default context window and truncates
            # long prompts (e.g. a full thesis) before the model ever sees them.
            options["num_ctx"] = self.num_ctx
        response = self.client.chat(
            model=self.model,
            messages=messages,
            tools=self.get_openai_tools(tools),
            options=options,
        )
        message = response["message"]
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return content, None
        normalized = []
        for tc in tool_calls:
            args = tc["function"].get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            normalized.append(
                ToolCall(
                    id=tc.get("id", tc["function"]["name"]),
                    name=tc["function"]["name"],
                    arguments=args,
                )
            )
        return content, normalized

    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result_content: str
    ) -> dict[str, Any]:
        return {"role": "tool", "content": result_content}


class OpenAIAdapter(ProviderAdapter, OpenAIToolsMixin):
    def __init__(self, client: openai_sdk.OpenAI, model: str, temperature: float = 0.1) -> None:
        super().__init__(model=model, temperature=temperature)
        self.client = client

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall] | None]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=self._to_openai_messages(messages),
            tools=self.get_openai_tools(tools),
            temperature=self.temperature,
        )
        message = response.choices[0].message
        content = message.content or ""
        if not message.tool_calls:
            return content, None
        normalized = []
        for tc in message.tool_calls:
            args = tc.function.arguments
            if isinstance(args, str):
                args = json.loads(args)
            normalized.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        return content, normalized

    @staticmethod
    def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalise assistant tool_calls to OpenAI's wire format.

        rainer's canonical history stores tool_calls as
        {"id", "function": {"name", "arguments": <dict>}} (what Ollama/Anthropic
        consume). The OpenAI/Azure API instead requires each tool_call to carry
        "type": "function" and a *string* "arguments". Rewrite only those messages;
        leave every other message (and every other adapter) untouched.
        """
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                fixed = []
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        args = json.dumps(args or {})
                    fixed.append(
                        {
                            "id": tc.get("id"),
                            "type": tc.get("type", "function"),
                            "function": {"name": fn.get("name"), "arguments": args},
                        }
                    )
                m = {**m, "tool_calls": fixed}
            out.append(m)
        return out

    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result_content: str
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_content,
        }


class AzureOpenAIAdapter(OpenAIAdapter):
    """Azure OpenAI uses the OpenAI-compatible API, so behaviour is identical to
    :class:`OpenAIAdapter`. The only differences (endpoint, api-version, and using
    the *deployment name* as the model id) live in the client passed in by the agent.
    """


class AzureResponsesAdapter(ProviderAdapter, OpenAIToolsMixin):
    """Azure OpenAI through the Responses API (v1 endpoint).

    Newer reasoning models (gpt-6.x) refuse function tools on Chat Completions, so this
    adapter maps rainer's canonical chat history onto Responses input items and back.
    Nothing is stored server-side (`store=False`); the full history is sent each call.
    No temperature is sent: these reasoning models reject any value but the default.
    """

    def __init__(self, client: openai_sdk.OpenAI, model: str, temperature: float = 0.1) -> None:
        super().__init__(model=model, temperature=temperature)
        self.client = client

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall] | None]:
        instructions, items = self._to_input_items(messages)
        response = self.client.responses.create(
            model=self.model,
            instructions=instructions or None,
            input=items,
            tools=self._to_response_tools(self.get_openai_tools(tools)),
            store=False,
        )
        texts: list[str] = []
        calls: list[ToolCall] = []
        for item in response.output:
            if item.type == "message":
                texts += [p.text for p in item.content if p.type == "output_text"]
            elif item.type == "function_call":
                args = json.loads(item.arguments) if item.arguments else {}
                calls.append(ToolCall(id=item.call_id, name=item.name, arguments=args))
        return "".join(texts), calls or None

    @staticmethod
    def _to_response_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Chat tools nest the schema under "function"; Responses tools are flat."""
        out = []
        for t in tools:
            fn = t.get("function", t)
            out.append(
                {
                    "type": "function",
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                }
            )
        return out

    @staticmethod
    def _to_input_items(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """System messages become `instructions`; assistant tool calls become
        `function_call` items and tool results `function_call_output` items."""
        instructions: list[str] = []
        items: list[dict[str, Any]] = []
        call_ids: set[str] = set()
        for m in messages:
            role, content = m.get("role"), m.get("content") or ""
            if role == "system":
                instructions.append(content)
            elif role == "tool":
                call_id = m.get("tool_call_id")
                if call_id in call_ids:
                    items.append(
                        {"type": "function_call_output", "call_id": call_id, "output": content}
                    )
                else:
                    # A result from an earlier chat() turn whose call is not in the history
                    # (memory keeps results, not calls); the API rejects an unmatched output.
                    items.append({"role": "user", "content": f"Earlier tool result:\n{content}"})
            else:
                if content:
                    items.append({"role": role, "content": content})
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", {})
                    args = fn.get("arguments")
                    if not isinstance(args, str):
                        args = json.dumps(args or {})
                    call_ids.add(tc.get("id"))
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": tc.get("id"),
                            "name": fn.get("name"),
                            "arguments": args,
                        }
                    )
        return "\n\n".join(instructions), items

    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result_content: str
    ) -> dict[str, Any]:
        return {"role": "tool", "tool_call_id": tool_call_id, "content": result_content}


class AnthropicAdapter(ProviderAdapter):
    def __init__(
        self, client: anthropic_sdk.Anthropic, model: str, temperature: float = 0.1
    ) -> None:
        super().__init__(model=model, temperature=temperature)
        self.client = client

    def _get_anthropic_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = []
        for tool in tools:
            func = tool["function"]
            converted.append(
                {
                    "name": func["name"],
                    "description": func["description"],
                    "input_schema": func["parameters"],
                }
            )
        return converted

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall] | None]:
        system_content = ""
        anthropic_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_content = msg["content"]
            elif msg["role"] == "tool":
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id", ""),
                                "content": msg["content"],
                            }
                        ],
                    }
                )
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                content_blocks = []
                if msg.get("content"):
                    content_blocks.append({"type": "text", "text": msg["content"]})
                for tc in msg["tool_calls"]:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", tc["function"]["name"]),
                            "name": tc["function"]["name"],
                            "input": tc["function"]["arguments"],
                        }
                    )
                anthropic_messages.append({"role": "assistant", "content": content_blocks})
            else:
                anthropic_messages.append(msg)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system_content,
            messages=anthropic_messages,
            tools=self._get_anthropic_tools(tools),
            temperature=self.temperature,
        )

        content = ""
        tool_calls: list[ToolCall] | None = None

        for block in response.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))
        return content, tool_calls

    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result_content: str
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_content,
        }


class GoogleAdapter(ProviderAdapter):
    """Adapter for Google Gemini with automatic function calling."""

    def __init__(
        self, client: genai.Client, model: str, tool_wrappers: list[Any], temperature: float = 0.1
    ) -> None:
        super().__init__(model=model, temperature=temperature)
        self.client = client
        self.tool_wrappers = tool_wrappers

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall] | None]:
        system_instruction = None
        history = []

        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            elif msg["role"] == "user":
                history.append(
                    google_types.Content(
                        role="user",
                        parts=[google_types.Part.from_text(text=msg["content"])],
                    )
                )
            elif msg["role"] == "tool":
                tool_name = msg.get("tool_name") or msg.get("tool_call_id") or "tool"
                tool_payload = msg.get("content", "")
                history.append(
                    google_types.Content(
                        role="user",
                        parts=[
                            google_types.Part.from_text(text=f"[Tool:{tool_name}] {tool_payload}")
                        ],
                    )
                )
            elif msg["role"] == "assistant":
                if msg.get("content"):
                    history.append(
                        google_types.Content(
                            role="model",
                            parts=[google_types.Part.from_text(text=msg["content"])],
                        )
                    )

        current_message = None
        if history and history[-1].role == "user":
            current_message = history.pop()

        if not current_message:
            return "", None

        tools = self.tool_wrappers if self.tool_wrappers else None
        config = google_types.GenerateContentConfig(
            tools=tools,
            system_instruction=system_instruction,
            generation_config=google_types.GenerationConfig(temperature=self.temperature),
        )

        chat = self.client.chats.create(
            model=self.model,
            history=history if history else None,
            config=config,
        )

        response = chat.send_message(current_message.parts[0].text)
        content = response.text if response.text else ""
        return content, None

    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result_content: str
    ) -> dict[str, Any]:
        return {"role": "tool", "tool_name": tool_name, "content": result_content}


def create_provider_adapter(
    provider: ProviderName,
    model: str,
    client: Any,
    google_tool_wrappers: list[Any] | None = None,
    temperature: float | None = None,
    num_ctx: int | None = None,
    azure_api: Literal["chat", "responses"] = "chat",
) -> ProviderAdapter:
    """Factory for provider adapters."""
    if temperature is None:
        temperature = get_config().provider.temperature

    if provider in ("ollama", "ollama-cloud"):
        return OllamaAdapter(
            client=client, model=model, temperature=temperature, num_ctx=num_ctx
        )
    if provider in ("openai", "openrouter"):
        return OpenAIAdapter(client=client, model=model, temperature=temperature)
    if provider == "azure-openai":
        if azure_api == "responses":
            return AzureResponsesAdapter(client=client, model=model, temperature=temperature)
        return AzureOpenAIAdapter(client=client, model=model, temperature=temperature)
    if provider == "anthropic":
        return AnthropicAdapter(client=client, model=model, temperature=temperature)
    if provider == "google":
        if google_tool_wrappers is None:
            google_tool_wrappers = []
        return GoogleAdapter(
            client=client,
            model=model,
            tool_wrappers=google_tool_wrappers,
            temperature=temperature,
        )
    raise ValueError(f"Unknown provider: {provider}")
