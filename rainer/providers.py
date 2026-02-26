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
    "anthropic",
    "google",
]


class ProviderAdapter(ABC):
    """Abstract adapter interface."""

    def __init__(self, model: str) -> None:
        self.model = model

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
    def __init__(self, client: ollama.Client, model: str) -> None:
        super().__init__(model=model)
        self.client = client

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall] | None]:
        response = self.client.chat(
            model=self.model,
            messages=messages,
            tools=self.get_openai_tools(tools),
        )
        message = response["message"]
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")
        if not tool_calls:
            return content, None
        normalized = [
            ToolCall(
                id=tc.get("id", tc["function"]["name"]),
                name=tc["function"]["name"],
                arguments=tc["function"].get("arguments", {}),
            )
            for tc in tool_calls
        ]
        return content, normalized

    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result_content: str
    ) -> dict[str, Any]:
        return {"role": "tool", "content": result_content}


class OpenAIAdapter(ProviderAdapter, OpenAIToolsMixin):
    def __init__(self, client: openai_sdk.OpenAI, model: str) -> None:
        super().__init__(model=model)
        self.client = client

    def generate(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall] | None]:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.get_openai_tools(tools),
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

    def format_tool_result(
        self, tool_call_id: str, tool_name: str, result_content: str
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_content,
        }


class AnthropicAdapter(ProviderAdapter):
    def __init__(self, client: anthropic_sdk.Anthropic, model: str) -> None:
        super().__init__(model=model)
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

    def __init__(self, client: genai.Client, model: str, tool_wrappers: list[Any]) -> None:
        super().__init__(model=model)
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
) -> ProviderAdapter:
    """Factory for provider adapters."""
    if provider in ("ollama", "ollama-cloud"):
        return OllamaAdapter(client=client, model=model)
    if provider in ("openai", "openrouter"):
        return OpenAIAdapter(client=client, model=model)
    if provider == "anthropic":
        return AnthropicAdapter(client=client, model=model)
    if provider == "google":
        if google_tool_wrappers is None:
            google_tool_wrappers = []
        return GoogleAdapter(client=client, model=model, tool_wrappers=google_tool_wrappers)
    raise ValueError(f"Unknown provider: {provider}")
