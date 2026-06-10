"""Azure OpenAI provider wiring (OpenAI-API-compatible)."""

from rainer.providers import AzureOpenAIAdapter, OpenAIAdapter, create_provider_adapter


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
