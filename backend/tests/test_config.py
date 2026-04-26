from app.config import get_allowed_origins


def test_get_allowed_origins_includes_local_defaults(monkeypatch):
    """Default CORS origins support local development."""
    monkeypatch.delenv("PHS_ALLOWED_ORIGINS", raising=False)

    origins = get_allowed_origins()

    assert origins == ["http://localhost:3000", "http://127.0.0.1:3000"]


def test_get_allowed_origins_reads_comma_separated_env(monkeypatch):
    """Runtime CORS origins can be configured for deployment."""
    monkeypatch.setenv(
        "PHS_ALLOWED_ORIGINS",
        "https://p01--frontend--ztkp7429rdfw.code.run, https://app.example.com ",
    )

    origins = get_allowed_origins()

    assert origins == [
        "https://p01--frontend--ztkp7429rdfw.code.run",
        "https://app.example.com",
    ]
