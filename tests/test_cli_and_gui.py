from unittest.mock import Mock

from click.testing import CliRunner

from meowth import cli
from meowth.gui.components.config_form import ConfigForm


def test_explicit_provider_does_not_inherit_other_provider_credentials(monkeypatch):
    monkeypatch.setattr(cli, "_load_config", lambda: {"translation": {
        "provider": "deepseek", "model": "deepseek-chat",
        "api": {"base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY"},
    }})
    assert cli._provider_kwargs("deepl", None, None, None) == {
        "provider": "deepl", "model": None, "api_base": None, "api_key_env": None,
    }


def test_cli_explicit_language_and_config_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_load_config", lambda: {"translation": {
        "source_language": "de", "target_language": "fr", "batch_size": 7, "max_workers": 2,
    }})
    assert cli._get_language("en", "en", "source_language") == "en"
    assert cli._get_language(None, "en", "source_language") == "de"
    engine = Mock()
    monkeypatch.setattr(cli, "TranslationEngine", engine)
    source = tmp_path / "texts.json"
    source.write_text("{}", encoding="utf-8")
    result = CliRunner().invoke(cli.main, ["translate", str(source), "--provider", "deepl", "--target", "it"])
    assert result.exit_code == 0, result.output
    config = engine.call_args.args[0]
    assert (config.source_lang, config.target_lang, config.batch_size, config.max_workers) == ("de", "it", 7, 2)
    engine.return_value.close.assert_called_once()


def test_gui_default_paths_and_environment_key(tmp_path, monkeypatch):
    form = Mock()
    rom = tmp_path / "test.gba"
    rom.write_bytes(b"test")
    values = {"provider": "deepl", "api_key_entry": "", "model_entry": "",
              "output_entry": "", "rom_entry": str(rom), "batch_size": "30", "max_workers": "2",
              "source_lang": "English", "target_lang": "Italian"}
    for field, value in values.items():
        getattr(form, field).get.return_value = value
    form._lang_name_to_code.side_effect = lambda language: {"English": "en", "Italian": "it"}[language]
    monkeypatch.setenv("DEEPL_API_KEY", "test-key")
    assert ConfigForm.validate(form) == (True, "")
    config = ConfigForm.get_config(form)
    assert config.output_dir is not None and config.work_dir is not None
    assert config.api_key_env == "DEEPL_API_KEY"
    form.api_key_entry.get.return_value = " first:fx, second:fx "
    assert ConfigForm.validate(form) == (True, "")
    assert ConfigForm.get_config(form).api_key == "first:fx, second:fx"
    form.api_key_entry.get.return_value = " , , "
    assert ConfigForm.validate(form) == (False, "Please enter your API key")
    form.api_key_entry.get.return_value = ""
    form.max_workers.get.return_value = "0"
    assert ConfigForm.validate(form)[0] is False


def test_gui_deepl_disables_model_and_other_provider_reenables_it():
    form = Mock()
    ConfigForm._update_provider_fields(form, "deepl")
    assert form.model_entry.configure.call_args.kwargs["state"] == "disabled"
    assert "virgola" in form.api_key_label.configure.call_args.kwargs["text"]
    form.reset_mock()
    ConfigForm._update_provider_fields(form, "deepseek")
    assert form.model_entry.configure.call_args_list[0].kwargs["state"] == "normal"
