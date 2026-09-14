import hashlib
import json
from unittest.mock import Mock

import pytest

from meowth.charmap import Charmap
from meowth.core.config import TranslationConfig
from meowth.core.engine import TranslationEngine
from meowth.italian_text import normalize_italian
from meowth import native_text
from meowth.rom_writer import RomWriter


@pytest.mark.parametrize("source,expected", [
    ("degli Pokémon", "dei Pokémon"),
    ("Gli Pokèmon", "I Pokémon"),
    ("dell'Pokémon", "del Pokémon"),
    ("dell’ Pokémon", "del Pokémon"),
    ("dellPokèmon", "del Pokémon"),
    (r"dagli\nPokémon\p[player]", r"dai\nPokémon\p[player]"),
    (r"dell'\nPokémon\CC0104", r"del\nPokémon\CC0104"),
    ("Dai ai Pokémon i loro strumenti.", "Dai ai Pokémon i loro strumenti."),
    ("L'acqua di Pikachu e Farfetch'd", "L'acqua di Pikachu e Farfetch'd"),
])
def test_italian_articles_preserve_number_names_and_controls(source, expected):
    assert normalize_italian(source, "it") == expected
    assert normalize_italian(expected, "it") == expected
    assert normalize_italian(source, "en") == source


def test_ascii_and_curly_apostrophes_are_encoded():
    charmap = Charmap(target_lang="it")
    assert charmap.encode("c'è l'acqua") == charmap.encode("c’è l’acqua")
    assert charmap.encode("c'è l'acqua").count(b"\xb4") == 2


def test_packaged_native_profiles_are_consistent():
    charmap = Charmap(target_lang="it")
    for profile in native_text._profiles():
        entries = profile["entries"]
        assert len({entry["id"] for entry in entries}) == len(entries)
        sources = [s for entry in entries for s in entry["pointer_sources"]]
        assert len(sources) == len(set(sources))
        for entry in entries:
            assert charmap.encode(entry["original"]) == bytes.fromhex(entry["expected_hex"])
            assert len(bytes.fromhex(entry["expected_hex"])) == entry["byte_length"]
            translation = entry["translations"]["it"]
            assert native_text.native_layout_ok(entry, translation)
            if not entry["is_pointer_based"]:
                assert len(charmap.encode(translation)) <= entry["byte_length"]


@pytest.fixture
def native_rom(monkeypatch):
    rom = bytearray(b"\xaa" * 0x200000)
    rom[0x1f0000:] = b"\xff" * 0x10000
    charmap = Charmap(target_lang="it")
    a, s = 0x110000, 0x100000
    raw = charmap.encode("OPTIONS")
    rom[a:a + len(raw)] = raw
    rom[s:s + 4] = (0x08000000 + a).to_bytes(4, "little")
    entry = dict(id="test_native", category="native_ui", address=hex(a),
                 original="OPTIONS", byte_length=len(raw), expected_hex=raw.hex(),
                 pointer_sources=[hex(s)], is_pointer_based=True,
                 translations={"it": "OPZIONI"}, max_lines=1, max_line_width=10)
    profile = dict(id="test", sha256=hashlib.sha256(rom).hexdigest(), entries=[entry])
    monkeypatch.setattr(native_text, "_profiles", lambda: (profile,))
    entry = native_text.native_entries(rom)[0]
    entry["translated"] = "OPZIONI"
    return rom, entry, s


def test_verified_native_reference_is_relocated(native_rom):
    rom, entry, source = native_rom
    writer = RomWriter(target_lang="it")
    writer.FONT_BOUNDARY = len(rom)
    out, stats = writer.inject_texts(rom, [entry])
    assert stats["relocated"] == 1
    target = int.from_bytes(out[source:source + 4], "little") - 0x08000000
    assert out[target:target + 8] == writer.charmap.encode("OPZIONI")


@pytest.mark.parametrize("changed", ["rom", "address", "source", "text", "layout", "strategy"])
def test_native_modifications_are_rejected_atomically(native_rom, changed):
    rom, entry, source = native_rom
    if changed == "rom":
        rom[0xc0] ^= 1
    elif changed == "address":
        entry["address"] = "0x110001"
    elif changed == "source":
        entry["pointer_sources"] = [hex(source + 4)]
    elif changed == "text":
        entry["original"] = "Other text"
    elif changed == "layout":
        entry["translated"] = r"OPZIONI\pIN ATTESA"
    else:
        entry["is_pointer_based"] = False
    before = bytes(rom)
    writer = RomWriter(target_lang="it")
    writer.FONT_BOUNDARY = len(rom)
    out, stats = writer.inject_texts(rom, [entry])
    assert out == before
    assert stats["skipped"] == 1


def test_native_translation_does_not_call_provider(native_rom, tmp_path):
    rom, entry, _ = native_rom
    provider = Mock()
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=provider)
    engine._translate_free_batch([entry])
    assert entry["translated"] == "OPZIONI"
    provider.translate_batch.assert_not_called()


def test_rebuild_old_json_adds_reviewed_native_text(native_rom, tmp_path):
    rom, _, source = native_rom
    original = tmp_path / "original.gba"
    original.write_bytes(rom)
    texts = tmp_path / "texts.json"
    texts.write_text(json.dumps({"tables": [], "free_texts": []}))
    out = tmp_path / "out.gba"
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=Mock())
    # The production ROM is already 32 MB. Keep this synthetic fixture's hash.
    from unittest.mock import patch
    with patch.object(RomWriter, "expand_rom", side_effect=lambda data: data):
        engine.build_rom(original, texts, out)
    result = out.read_bytes()
    assert result[source:source + 4] != rom[source:source + 4]
    assert original.read_bytes() == rom


def test_button_labels_preserve_icons_and_no_pages():
    entry = {"category": "native_ui", "original": r"\btn03NEXT", "max_lines": 1, "max_line_width": 8}
    assert native_text.native_layout_ok(entry, r"\btn03VAI")
    assert not native_text.native_layout_ok(entry, r"\btn02VAI")
    assert not native_text.native_layout_ok(entry, r"\btn03VAI\p")


@pytest.mark.parametrize("translated,accepted", [("SCELTE", True), ("IMPOSTAZIONI", False)])
def test_native_inline_text_never_truncates_or_searches_pointers(native_rom, monkeypatch, translated, accepted):
    rom, entry, _ = native_rom
    profile = native_text._profiles()[0]
    profile["entries"][0].update(is_pointer_based=False, pointer_sources=[], max_line_width=20)
    entry = native_text.native_entries(rom)[0]
    entry["translated"] = translated
    writer = RomWriter(target_lang="it")
    writer.FONT_BOUNDARY = len(rom)
    search = Mock(side_effect=AssertionError("Native text must not trigger pointer scanning"))
    monkeypatch.setattr(writer, "_search_pointers", search)
    before = bytes(rom)
    out, stats = writer.inject_texts(rom, [entry])
    assert stats["in_place"] == int(accepted)
    assert (out != before) == accepted
    search.assert_not_called()
