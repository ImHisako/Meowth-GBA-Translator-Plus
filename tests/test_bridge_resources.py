import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from meowth import resource_path
from meowth.binaries.loader import find_meowth_bridge_resources
from meowth.core.engine import TranslationEngine


def make_resources(directory, content="metadata"):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "default.toml").write_text(content, encoding="utf-8")
    return directory


def test_bridge_resources_prefer_selected_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(resource_path, "get_resource_path", lambda path: tmp_path / path)
    bundled = make_resources(tmp_path / "binary" / "resources")
    make_resources(tmp_path / "resources", "glossary bundle")
    assert find_meowth_bridge_resources(tmp_path / "binary" / "bridge") == bundled


@pytest.mark.parametrize("fallback", [
    "resources", "HexManiacAdvance/src/HexManiac.Core/Models/Code",
])
def test_bridge_resources_development_fallback(tmp_path, monkeypatch, fallback):
    monkeypatch.setattr(resource_path, "get_resource_path", lambda path: tmp_path / path)
    expected = make_resources(tmp_path / fallback)
    assert find_meowth_bridge_resources(tmp_path / "binary" / "bridge") == expected


def test_glossary_only_resources_fail_before_launch(tmp_path, monkeypatch):
    monkeypatch.setattr(resource_path, "get_resource_path", lambda path: tmp_path / path)
    glossary = tmp_path / "resources"
    glossary.mkdir()
    (glossary / "glossary_en_it.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(TranslationEngine, "find_meowth_bridge", lambda: tmp_path / "bridge")
    run = Mock()
    monkeypatch.setattr("meowth.core.engine.subprocess.run", run)
    with pytest.raises(FileNotFoundError, match="missing default.toml"):
        TranslationEngine.extract_texts(tmp_path / "rom.gba", tmp_path / "work" / "text.json")
    run.assert_not_called()


@pytest.mark.parametrize("existing", [False, True])
def test_extract_stages_resources_and_reads_bridge_output(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(resource_path, "get_resource_path", lambda path: tmp_path / "app" / path)
    exe = tmp_path / "cached bridge" / "bridge.exe"
    make_resources(exe.parent / "resources")
    (exe.parent / "resources" / "default.bpee.toml").write_text("emerald", encoding="utf-8")
    monkeypatch.setattr(TranslationEngine, "find_meowth_bridge", lambda: exe)
    output = tmp_path / "work" / "extracted.json"
    (tmp_path / "Pokémon.gba").write_bytes(b"\0" * 256)
    if existing:
        make_resources(output.parent / "resources", "outdated")

    def extract(command, **kwargs):
        cwd = Path(kwargs["cwd"])
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        assert command == [str(exe), "extract", str(tmp_path / "Pokémon.gba")]
        assert (cwd / "resources" / "default.toml").read_text() == "metadata"
        assert (cwd / "resources" / "default.bpee.toml").read_text() == "emerald"
        (cwd / "work").mkdir()
        (cwd / "work" / "text.json").write_text('{"entries": []}', encoding="utf-8")
        return Mock(returncode=0)

    monkeypatch.setattr("meowth.core.engine.subprocess.run", extract)
    assert TranslationEngine.extract_texts(tmp_path / "Pokémon.gba", output) == output
    assert json.loads(output.read_text()) == {"entries": []}
