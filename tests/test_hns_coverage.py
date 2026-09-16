"""Regression coverage for HnS tables, executable literals and Italian layout."""
import hashlib
import json
import re
from unittest.mock import Mock

import pytest

from meowth import native_text
from meowth.charmap import Charmap
from meowth.control_codes import protect, restore
from meowth.core.config import TranslationConfig
from meowth.core.engine import TranslationEngine
from meowth.rom_writer import RomWriter
from meowth.text_wrap import wrap_text, _TOKEN_RE, _token_width


@pytest.mark.parametrize("ending", [".", "?", "!", "...", r"\."])
def test_latin_punctuation_stays_with_word_within_width(ending):
    wrapped = wrap_text("Uno due tre quattro" + ending, line_width=19, target_lang="it")
    for line in re.split(r"\\[np]", wrapped):
        assert not re.fullmatch(r"[.!?]+|\\\.", line)
        assert sum(_token_width(m.group()) for m in _TOKEN_RE.finditer(line)) <= 19


def test_italian_wrap_balances_single_final_word():
    assert wrap_text("Uno due tre quattro cinque", line_width=20, target_lang="it") == r"Uno due tre\nquattro cinque"


def test_punctuation_stays_attached_across_color_control():
    assert wrap_text(r"Uno due tre\CC0102?", line_width=11, target_lang="it") == r"Uno due\ntre\CC0102?"


def test_reflow_drops_english_line_lengths_but_keeps_paragraphs_and_variables():
    original = "Hello\r\n[player]!\r\n\r\nHow are\r\nyou?\\p"
    text, codes = protect(original, reflow=True)
    assert restore(text, codes) == "Hello [player]!\n\nHow are you?\\p"


def test_profiles_for_same_rom_are_combined(monkeypatch):
    rom = b"test"
    digest = hashlib.sha256(rom).hexdigest()
    monkeypatch.setattr(native_text, "_profiles", lambda: (
        dict(id="one", sha256=digest, entries=[dict(id="first")]),
        dict(id="two", sha256=digest, entries=[dict(id="second")]),
    ))
    assert [entry["native_profile"] for entry in native_text.native_entries(rom)] == ["one", "two"]
    assert native_text.native_entries(rom + b"modified") == []


def test_hns_profile_contains_missing_categories():
    profile = next(p for p in native_text._profiles() if p["id"] == "hns-2.0.5-battle-items")
    categories = {e["category"] for e in profile["entries"]}
    assert categories == {"native_item_names", "native_item_descriptions", "native_battle", "native_trainer"}
    assert any(e["original"] == "POTION" for e in profile["entries"])
    assert any("challenged by" in e["original"] for e in profile["entries"])
    assert any("I just lost" in e["original"] for e in profile["entries"])


@pytest.mark.parametrize("category,original,translated", [
    ("native_battle", r"\0F used \34!", r"\0F usa \34!"),
    ("native_trainer", r"Hello [player]!", r"Ciao\n[player]!"),
])
def test_dynamic_native_variables_must_be_preserved(category, original, translated):
    entry = dict(category=category, original=original)
    assert native_text.native_layout_ok(entry, translated)
    assert not native_text.native_layout_ok(entry, "Testo senza variabili")


def test_verified_low_literal_is_written_but_unprofiled_reference_is_rejected(monkeypatch):
    rom = bytearray(b"\xaa" * 0x200000)
    rom[0x1f0000:] = b"\xff" * 0x10000
    address, source, instruction = 0x110000, 0x800, 0x7f0
    charmap = Charmap(target_lang="it")
    raw = charmap.encode(r"\0F used \34!")
    rom[address:address+len(raw)] = raw
    rom[source:source+4] = (0x08000000 + address).to_bytes(4, "little")
    rom[instruction:instruction+2] = bytes.fromhex("0348")
    entry = dict(id="battle", category="native_battle", original=r"\0F used \34!",
                 address=hex(address), byte_length=len(raw), expected_hex=raw.hex(),
                 pointer_sources=[hex(source)], is_pointer_based=True,
                 literal_instructions={hex(source): hex(instruction)})
    profile = dict(id="test", sha256=hashlib.sha256(rom).hexdigest(), entries=[entry])
    monkeypatch.setattr(native_text, "_profiles", lambda: (profile,))
    entry = native_text.native_entries(rom)[0]
    entry["translated"] = r"\0F usa \34!"
    writer = RomWriter(target_lang="it")
    writer.FONT_BOUNDARY = len(rom)
    before = bytes(rom)
    out, stats = writer.inject_texts(bytearray(rom), [entry])
    assert stats["relocated"] == 1
    assert out[source:source+4] != before[source:source+4]
    assert out[instruction:instruction+2] == before[instruction:instruction+2]
    entry.pop("native_profile")
    entry["category"] = "battle_text"
    out, stats = writer.inject_texts(bytearray(rom), [entry])
    assert out == before
    assert stats["unsafe_ptrs"] == 1


def test_static_item_description_never_receives_page_breaks(tmp_path):
    provider = Mock()
    provider.translate_batch.return_value = ["Ripristina venti punti salute di un Pokémon."]
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=provider)
    entry = dict(category="native_item_descriptions", original="Restores twenty HP.", max_lines=3, max_line_width=18)
    engine._translate_free_batch([entry])
    assert native_text.native_layout_ok(entry, entry["translated"])
    assert r"\p" not in entry["translated"]


def test_currency_glyph_survives_encoding():
    assert Charmap(target_lang="it").encode(r"$\00") == bytes.fromhex("b7fd00ff")


def test_battle_variables_are_hidden_from_translation_provider():
    text, codes = protect(r"\0F used \34!")
    assert text == "{C0} used {C1}!"
    assert restore("{C0} usa {C1}!", codes) == r"\0F usa \34!"
