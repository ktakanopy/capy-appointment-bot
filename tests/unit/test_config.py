import pytest

from app.config import load_settings


def test_settings_preserve_tracing_and_provider_environment(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    monkeypatch.setenv("UNDERSTANDING_PROVIDER", "GEMMA")
    assert load_settings().tracing_enabled is True
    assert load_settings().understanding_provider == "gemma"

    monkeypatch.setenv("TRACING_ENABLED", "off")
    assert load_settings().tracing_enabled is False


@pytest.mark.parametrize("value", ["0", "nan", "inf"])
def test_audio_timeout_must_be_finite_and_positive(monkeypatch, value):
    monkeypatch.setenv("TELEGRAM_AUDIO_CHUNK_TIMEOUT_SECONDS", value)
    with pytest.raises(ValueError):
        load_settings()
