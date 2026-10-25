"""The pipeline must stay fully local: no cloud LLM endpoints, no telemetry."""
import pytest

from narravid import tools
from narravid.llm import LLM, LLMError, ensure_local


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://127.0.0.1:1234", "http://[::1]:8080",
                                 "http://192.168.1.20:11434", "http://10.0.0.5:1234"])
def test_local_endpoints_allowed(url):
    assert ensure_local(url) == url


@pytest.mark.parametrize("url", ["https://api.anthropic.com", "https://api.openai.com/v1",
                                 "http://8.8.8.8:11434", "https://generativelanguage.googleapis.com"])
def test_remote_endpoints_refused(url):
    with pytest.raises(LLMError):
        ensure_local(url)


def test_llm_adapter_refuses_remote_config(cfg):
    cfg["llm"]["base_url"] = "https://api.anthropic.com"
    with pytest.raises(LLMError):
        LLM(cfg)


def test_node_processes_have_telemetry_disabled():
    env = tools.node_env()
    for k in ("HYPERFRAMES_NO_TELEMETRY", "DO_NOT_TRACK", "HYPERFRAMES_NO_UPDATE_CHECK"):
        assert env[k] == "1"


def test_huggingface_offline():
    import os
    import narravid  # noqa: F401
    assert os.environ["HF_HUB_OFFLINE"] == "1"
