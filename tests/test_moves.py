import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from meowth.charmap import Charmap
from meowth.control_codes import restore
from meowth.core.config import TranslationConfig
from meowth.core.engine import TranslationEngine
from meowth.glossary import Glossary
from meowth.move_glossary import MoveGlossary
from meowth.rom_writer import RomWriter
from meowth.translator import Translator
from meowth.translation_validation import TranslationValidationError, validate_translation


@pytest.mark.parametrize("source,expected", [
    ("THUNDERBOLT", "Fulmine"), ("FLAMETHROWER", "Lanciafiamme"),
    ("ICE BEAM", "Geloraggio"), ("PSYCHIC", "Psichico"),
    ("THUNDERPUNCH", "Tuonopugno"), ("VICEGRIP", "Presa"),
    ("FAINT ATTACK", "Finta"), ("HI JUMP KICK", "Calcinvolo"),
    ("SMELLINGSALT", "Maniereforti"),
])
def test_official_moves_and_gba_aliases(source, expected):
    assert MoveGlossary().lookup(source) == expected


def test_psychic_move_does_not_collide_with_type(tmp_path):
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=Mock())
    moves = {"category": "move_names", "entries": [{"original": "PSYCHIC"}]}
    types = {"category": "type_names", "entries": [{"original": "PSYCHIC"}]}
    engine._translate_table(moves)
    engine._translate_table(types)
    assert moves["entries"][0]["translated"] == "Psichico"
    assert types["entries"][0]["translated"] == "Psico"
    engine.translator.translate_batch.assert_not_called()


def test_all_354_gba_moves_translate_and_fit_real_slots(tmp_path):
    import meowth.move_glossary
    records = json.loads((Path(meowth.move_glossary.__file__).parent / "data/moves_it.json")
                         .read_text(encoding="utf-8"))["moves"]
    records = [record for record in records if 1 <= record["id"] <= 354]
    assert len(records) == 354
    charmap = Charmap(target_lang="it")
    writer = RomWriter(charmap, target_lang="it")
    base = writer.MIN_POINTER_SOURCE + 0x100
    rom = bytearray([0xAA]) * (base + 354 * 13 + 16)
    entries = []
    for offset, record in enumerate(records):
        address = base + offset * 13
        original = charmap.encode(record["gba_en"])
        assert len(original) <= 13
        rom[address:address + 13] = original + b"\xFF" * (13 - len(original))
        entries.append({"original": record["gba_en"], "address": hex(address),
                        "category": "move_names", "table_name": "data.pokemon.moves.names",
                        "table_index": record["id"], "byte_length": 13, "is_pointer_based": False})
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=Mock())
    table = {"category": "move_names", "entries": entries}
    engine._translate_table(table)
    stats = {"in_place": 0, "skipped": 0}
    for entry in entries:
        assert engine.moves.lookup(entry["original"]) is not None
        encoded = charmap.encode(entry["translated"])
        assert len(encoded) <= 13, entry
        writer._process_entry_v2(rom, entry, stats)
        address = int(entry["address"], 16)
        assert rom[address:address + len(encoded)] == encoded, entry
    assert rom[:base] == b"\xAA" * base
    assert rom[-16:] == b"\xAA" * 16
    engine.translator.translate_batch.assert_not_called()


def test_full_or_historical_name_depends_on_slot():
    moves, charmap = MoveGlossary(), Charmap(target_lang="it")
    assert moves.fit_name("QUICK ATTACK", charmap, 13) == "Att. Rapido"
    assert moves.fit_name("QUICK ATTACK", charmap, 30) == "Attacco Rapido"
    assert moves.fit_name("QUICK ATTACK", charmap, 3) is None


def test_unknown_hack_move_is_not_replaced_by_species_or_guessed(tmp_path):
    translator, callbacks = Mock(), Mock()
    engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), callbacks, translator=translator)
    table = {"category": "move_names", "entries": [
        {"original": "EMBER BURST", "table_index": 85}, {"original": "PIKACHU"}
    ]}
    engine._translate_table(table)
    assert [entry["translated"] for entry in table["entries"]] == ["EMBER BURST", "PIKACHU"]
    assert callbacks.on_log.call_count == 2
    translator.translate_batch.assert_not_called()


def test_explicit_references_and_ordinary_words():
    moves = MoveGlossary()
    for source in ["Please cut the rope and rest here.", "The PSYCHIC type is strong.", "I WILL CUT A TREE."]:
        assert moves.protect(source) == (source, [])
    protected, names = moves.protect("PIKACHU used THUNDERBOLT! It knows CUT.")
    assert protected == "PIKACHU used {M0}! It knows {M1}."
    assert restore(protected, names) == "PIKACHU used Fulmine! It knows Taglio."
    protected, names = moves.protect("It knows THUNDERBOLT, ICE BEAM and FLAMETHROWER.")
    assert restore(protected, names) == "It knows Fulmine, Geloraggio and Lanciafiamme."


def test_move_placeholders_are_validated():
    with pytest.raises(TranslationValidationError):
        validate_translation("used {M0}", "usa Tuono")
    with pytest.raises(TranslationValidationError):
        validate_translation("used {M0}", "usa {M0} {M0}")
    validate_translation("{M0} and {M1}", "{M1} e {M0}")


def test_deepl_preserves_moves_and_species_together(tmp_path):
    translator = Translator(provider="deepl", api_key="test:fx", target_lang="it", cache_dir=tmp_path)
    def handle(request):
        body = json.loads(request.content)
        assert body["text"] == ["<text><keep>{P0}</keep> used <keep>{M0}</keep>!</text>"]
        return httpx.Response(200, json={"translations": [{"text": body["text"][0].replace("used", "usa")}]})
    translator._client.close()
    translator._client = httpx.Client(transport=httpx.MockTransport(handle))
    try:
        engine = TranslationEngine(TranslationConfig(target_lang="it", work_dir=tmp_path), translator=translator)
        batch = [{"original": "PIKACHU used THUNDERBOLT!"}]
        engine._translate_free_batch(batch)
        assert batch[0]["translated"] == "PIKACHU usa Fulmine!"
    finally:
        translator.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_overlong_move_is_never_truncated_or_relocated(legacy):
    writer = RomWriter(Charmap(target_lang="it"), target_lang="it")
    base = writer.MIN_POINTER_SOURCE + 0x100
    rom = bytearray([0xAA]) * (base + 32)
    original = writer.charmap.encode("CUT")
    rom[base:base + 5] = original + b"\xFF"
    before = bytes(rom)
    entry = {"category": "move_names", "address": hex(base), "original": "CUT", "translated": "Lanciafiamme",
             "byte_length": 5, "table_name": "data.pokemon.moves.names", "table_index": 15}
    stats = {"skipped": 0}
    if legacy:
        writer._process_entry(rom, entry, stats)
    else:
        writer._process_entry_v2(rom, entry, stats)
    assert stats["skipped"] == 1
    assert bytes(rom) == before


def test_non_italian_translation_unchanged():
    assert MoveGlossary(target_lang="fr").protect("used CUT") == ("used CUT", [])


def test_other_source_languages_have_exact_move_names():
    assert MoveGlossary(source_lang="fr").lookup("Tonnerre") == "Fulmine"
