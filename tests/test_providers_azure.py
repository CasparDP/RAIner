"""Azure OpenAI provider wiring (OpenAI-API-compatible)."""

from types import SimpleNamespace as _NS

from rainer.config import AzureOpenAIConfig
from rainer.providers import (
    AzureOpenAIAdapter,
    AzureResponsesAdapter,
    OpenAIAdapter,
    create_provider_adapter,
)


class _FakeMessage:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def create(self, **kwargs):
        return _FakeResponse(_FakeMessage("hello from azure", None))


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self):
        self.chat = _FakeChat()


def test_factory_returns_azure_adapter():
    adapter = create_provider_adapter(
        "azure-openai", "my-deployment", client=_FakeClient(), temperature=0.0
    )
    assert isinstance(adapter, AzureOpenAIAdapter)
    assert isinstance(adapter, OpenAIAdapter)  # reuses the OpenAI-compatible path
    assert adapter.model == "my-deployment"


def test_azure_adapter_generate():
    adapter = create_provider_adapter(
        "azure-openai", "my-deployment", client=_FakeClient(), temperature=0.0
    )
    content, tool_calls = adapter.generate(
        messages=[{"role": "user", "content": "hi"}], tools=[]
    )
    assert content == "hello from azure"
    assert tool_calls is None


# --- Responses API adapter (AZURE_OPENAI_API=responses) ----------------------

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": "Search",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]


class _FakeResponses:
    def __init__(self, output):
        self.output = output
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return _NS(output=self.output)


def _responses_adapter(output):
    client = _NS(responses=_FakeResponses(output))
    adapter = create_provider_adapter(
        "azure-openai", "gpt-6.1-sol", client=client, temperature=1.0, azure_api="responses"
    )
    return adapter, client.responses


def test_factory_picks_responses_adapter_only_when_asked():
    adapter, _ = _responses_adapter([])
    assert isinstance(adapter, AzureResponsesAdapter)
    default = create_provider_adapter("azure-openai", "d", client=_FakeClient(), temperature=0.0)
    assert not isinstance(default, AzureResponsesAdapter)


def test_responses_adapter_maps_history_and_tools():
    adapter, fake = _responses_adapter(
        [
            _NS(type="reasoning"),
            _NS(type="message", content=[_NS(type="output_text", text="Looking up papers.")]),
            _NS(
                type="function_call", call_id="c2", name="search_papers", arguments='{"query": "x"}'
            ),
        ]
    )
    history = [
        {"role": "system", "content": "You review drafts."},
        {"role": "tool", "content": "old result", "tool_call_id": "orphan"},
        {"role": "user", "content": "Review my draft."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "function": {"name": "search_papers", "arguments": {"query": "q"}}}
            ],
        },
        adapter.format_tool_result("c1", "search_papers", '{"success": true}'),
    ]
    content, calls = adapter.generate(history, _TOOLS)

    sent = fake.kwargs
    assert sent["instructions"] == "You review drafts."
    assert sent["store"] is False
    assert sent["tools"] == [
        {
            "type": "function",
            "name": "search_papers",
            "description": "Search",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
    ]
    assert sent["input"] == [
        {"role": "user", "content": "Earlier tool result:\nold result"},
        {"role": "user", "content": "Review my draft."},
        {
            "type": "function_call",
            "call_id": "c1",
            "name": "search_papers",
            "arguments": '{"query": "q"}',
        },
        {"type": "function_call_output", "call_id": "c1", "output": '{"success": true}'},
    ]
    assert content == "Looking up papers."
    assert [(c.id, c.name, c.arguments) for c in calls] == [("c2", "search_papers", {"query": "x"})]


def test_responses_adapter_plain_answer_has_no_tool_calls():
    adapter, _ = _responses_adapter(
        [_NS(type="message", content=[_NS(type="output_text", text="Report")])]
    )
    assert adapter.generate([{"role": "user", "content": "hi"}], []) == ("Report", None)


def test_azure_api_setting_reads_env(monkeypatch):
    monkeypatch.delenv("AZURE_OPENAI_API", raising=False)
    assert AzureOpenAIConfig().api == "chat"
    monkeypatch.setenv("AZURE_OPENAI_API", "responses")
    assert AzureOpenAIConfig().api == "responses"
