from config.settings import Settings


def _settings(**over) -> Settings:
    return Settings(database_url="postgresql+asyncpg://x/y", jwt_secret="s", **over)


def test_the_default_is_the_vite_dev_server():
    assert _settings().cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_origins_are_comma_separated_trimmed_and_lose_a_trailing_slash():
    s = _settings(cors_allowed_origins=" https://ar.example.com/ , https://admin.example.com,, ")
    assert s.cors_origins == ["https://ar.example.com", "https://admin.example.com"]


def test_an_empty_setting_allows_no_origin():
    assert _settings(cors_allowed_origins="").cors_origins == []
