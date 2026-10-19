from yuxi.models.rerank import get_rerank_timeout_seconds


def test_rerank_timeout_defaults_to_30_seconds(monkeypatch):
    monkeypatch.delenv("RERANK_TIMEOUT_SECONDS", raising=False)

    assert get_rerank_timeout_seconds() == 30.0


def test_rerank_timeout_reads_env_override(monkeypatch):
    monkeypatch.setenv("RERANK_TIMEOUT_SECONDS", "180")

    assert get_rerank_timeout_seconds() == 180.0


def test_rerank_timeout_ignores_invalid_values(monkeypatch):
    monkeypatch.setenv("RERANK_TIMEOUT_SECONDS", "abc")
    assert get_rerank_timeout_seconds() == 30.0

    monkeypatch.setenv("RERANK_TIMEOUT_SECONDS", "-5")
    assert get_rerank_timeout_seconds() == 30.0

    monkeypatch.setenv("RERANK_TIMEOUT_SECONDS", "0")
    assert get_rerank_timeout_seconds() == 30.0
