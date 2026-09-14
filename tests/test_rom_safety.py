import pytest

from meowth.charmap import Charmap
from meowth.pcs_scanner import is_message_pointer, is_real_text
from meowth.rom_writer import RomWriter


def sample_rom(prefix=b"\x0f\x00", suffix=b"\x09\x06"):
    rom = bytearray(b"\xAA" * 0x200000)
    rom[0x1F0000:] = b"\xFF" * 0x10000
    target, source = 0x110000, 0xAC60B
    text = Charmap(target_lang="it").encode("Hello friend")
    rom[target:target + len(text)] = text
    rom[source - 2:source + 6] = prefix + (0x08000000 + target).to_bytes(4, "little") + suffix
    entry = {"id": "script", "category": "scripts", "address": hex(target), "pointer_sources": [hex(source)],
             "original": "Hello friend", "translated": "Ciao amico", "byte_length": len(text), "is_pointer_based": True}
    return rom, entry, source, target


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("prefix,suffix", [
    (b"\x0f\x02", b"\xad\x2a"),  # Thumb code mistaken for loadpointer at 0xAC60B.
    (b"\x0f\x00", b"\x29\x18"),  # Second real crash pattern at 0x10CA1F.
    (b"\x0f\x00", b"\x40\x00"),  # Graphics pointing into readable text by coincidence.
])
def test_false_script_references_never_modify_rom(legacy, prefix, suffix):
    rom, entry, _, _ = sample_rom(prefix, suffix)
    before = bytes(rom)
    writer = RomWriter(target_lang="it")
    writer.FONT_BOUNDARY = len(rom)
    writer.write_offset = 0x1F0000
    stats = {"skipped": 0}
    if legacy:
        writer._process_entry(rom, entry, stats)
    else:
        writer._process_entry_v2(rom, entry, stats)
    assert rom == before
    assert stats["skipped"] == 1
    assert writer.write_offset == 0x1F0000


def test_real_unaligned_msgbox_pointer_is_relocated():
    rom, entry, source, target = sample_rom()
    before = bytes(rom)
    writer = RomWriter(target_lang="it")
    writer.FONT_BOUNDARY = len(rom)
    result, stats = writer.inject_texts(rom, [entry])
    assert stats["relocated"] == 1
    relocated = int.from_bytes(result[source:source + 4], "little") - 0x08000000
    encoded = writer.charmap.encode("Ciao amico")
    assert result[relocated:relocated + len(encoded)] == encoded
    assert result[target:target + entry["byte_length"]] == before[target:target + entry["byte_length"]]
    assert result[:source] == before[:source]
    assert result[source + 4:relocated] == before[source + 4:relocated]


@pytest.mark.parametrize("source", [-1, 0x1FFFFF, 0xAC610])
def test_invalid_or_stale_pointer_is_rejected_atomically(source):
    rom, entry, _, _ = sample_rom()
    entry["pointer_sources"].append(hex(source))
    before = bytes(rom)
    writer = RomWriter(target_lang="it")
    writer.FONT_BOUNDARY = len(rom)
    result, stats = writer.inject_texts(rom, [entry])
    assert result == before
    assert stats["skipped"] == 1


def test_undefined_hma_bytes_are_not_mistaken_for_text():
    assert not is_real_text(r'"Hello readable letters \!4A with more letters"')
    assert is_real_text(r'"Hello [player]!\pHow are you?"')


def test_pointer_into_header_is_rejected():
    rom, _, source, _ = sample_rom()
    rom[source:source + 4] = (0x08000011).to_bytes(4, "little")
    assert not is_message_pointer(rom, 0x11, source)
