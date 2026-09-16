import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from meowth.credentials import CredentialStore, CredentialsError, credentials_path
from meowth.gui.components.config_form import ConfigForm
from meowth.translator import Translator


def test_keys_survive_reload_and_remain_separate(tmp_path):
    path = tmp_path / "api_keys.json"
    store = CredentialStore(path)
    store.save("deepl", " first:fx, second:fx ")
    store.save("deepseek", "other-test-key")
    reloaded = CredentialStore(path)
    assert reloaded.get("deepl") == "first:fx, second:fx"
    assert reloaded.get("deepseek") == "other-test-key"
    reloaded.save("deepl", "")
    assert reloaded.get("deepl") == ""
    assert reloaded.get("deepseek") == "other-test-key"
    assert not list(tmp_path.glob(".api_keys-*.tmp"))


@pytest.mark.parametrize("content", ["invalid-secret-json", '[]', '{"version": 1, "providers": {"deepl": 123}}'])
def test_corrupt_file_is_not_overwritten_or_exposed(tmp_path, content):
    path = tmp_path / "api_keys.json"
    path.write_text(content)
    with pytest.raises(CredentialsError) as error:
        CredentialStore(path).save("deepl", "new-test-key")
    assert path.read_text() == content
    assert content not in str(error.value)
    assert "new-test-key" not in str(error.value)


def test_atomic_write_failure_preserves_existing_keys(tmp_path, monkeypatch):
    path = tmp_path / "api_keys.json"
    store = CredentialStore(path)
    store.save("deepl", "old-test-key")
    monkeypatch.setattr("meowth.credentials.os.replace", Mock(side_effect=OSError("secret-internal-error")))
    with pytest.raises(CredentialsError) as error:
        store.save("deepl", "new-test-key")
    assert store.get("deepl") == "old-test-key"
    assert "secret-internal-error" not in str(error.value)
    assert not list(tmp_path.glob(".api_keys-*.tmp"))


def test_translator_uses_saved_keys_with_explicit_and_environment_precedence(tmp_path, monkeypatch):
    path = tmp_path / "api_keys.json"
    monkeypatch.setenv("MEOWTH_API_KEYS_FILE", str(path))
    monkeypatch.delenv("DEEPL_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert credentials_path() == path
    CredentialStore().save("deepl", "first:fx,second:fx")
    translator = Translator(provider="deepl", cache_dir=tmp_path / "cache")
    assert translator._deepl_keys == ["first:fx", "second:fx"]
    translator.close()
    other = Translator(provider="deepseek", cache_dir=tmp_path / "cache")
    assert other.api_key == ""
    other.close()
    monkeypatch.setenv("DEEPL_API_KEY", "environment:fx")
    translator = Translator(provider="deepl", cache_dir=tmp_path / "cache")
    assert translator.api_key == "environment:fx"
    translator.close()
    translator = Translator(provider="deepl", api_key="explicit:fx", cache_dir=tmp_path / "cache")
    assert translator.api_key == "explicit:fx"
    translator.close()


def test_gui_provider_switch_saves_and_restores_without_reusing_wrong_key(tmp_path):
    store = CredentialStore(tmp_path / "api_keys.json")
    store.save("deepl", "saved-deepl:fx")
    field = Mock()
    state = {"value": "entered-deepseek"}
    field.get.side_effect = lambda: state["value"]
    field.delete.side_effect = lambda *args: state.update(value="")
    field.insert.side_effect = lambda index, value: state.update(value=value)
    form = SimpleNamespace(credential_store=store, _active_provider="deepseek", api_key_entry=field,
                           key_status=Mock(), provider=Mock(), _update_provider_fields=Mock())
    form.save_api_key = lambda: ConfigForm.save_api_key(form)
    ConfigForm._on_provider_change(form, "deepl")
    assert store.get("deepseek") == "entered-deepseek"
    assert field.get() == "saved-deepl:fx"
    assert store.load()["last_provider"] == "deepl"
    ConfigForm._on_provider_change(form, "google")
    assert field.get() == ""
    assert store.get("google") == ""


def test_gui_reports_save_failure_without_key_contents():
    form = SimpleNamespace(credential_store=Mock(), _active_provider="deepl",
                           api_key_entry=Mock(), key_status=Mock())
    form.api_key_entry.get.return_value = "private-test-key"
    form.credential_store.save.side_effect = CredentialsError("Salvataggio fallito")
    assert not ConfigForm.save_api_key(form)
    assert "private-test-key" not in str(form.key_status.configure.call_args)
