import pytest


@pytest.fixture(autouse=True)
def isolated_api_key_file(tmp_path, monkeypatch):
    """Tests must never read or overwrite the user's saved credentials."""
    monkeypatch.setenv("MEOWTH_API_KEYS_FILE", str(tmp_path / "api_keys.json"))
