"""Generate the additional text manifest for the exact HnS 2.0.5 ROM.

Run with the original ROM as the sole argument. No ROM is modified. Layouts
come from HnS's ItemInfo and trainerbattle macro; all decoded strings must
round-trip byte for byte. This is deliberately not a generic pointer scanner.
"""
import hashlib
import json
from pathlib import Path
import sys

from meowth.charmap import Charmap
from meowth.glossary import Glossary
from meowth.pcs_codes import PCS_CHAR_TABLE, FD_MACROS, fc_arg_count

SHA256 = "edf76ecf2a1c23a65c62ab63b1c0e775965978c81baeed20e249e96b3417679b"
ITEM_BASE, ITEM_COUNT, ITEM_STRIDE = 0x78F0F0, 901, 44
BATTLE_START, BATTLE_END = 0x4777E8, 0x4782B0


def decode(rom, address, *, battle=False):
    parts, pos = [], address
    while pos < min(address + 2048, len(rom)):
        value = rom[pos]
        pos += 1
        if value == 0xFF:
            text = "".join(parts)
            raw = rom[address:pos]
            if Charmap(target_lang="it").encode(text) != raw:
                raise ValueError(f"Non-round-tripping text at {address:x}")
            return text, raw
        if value in (0xFA, 0xFB, 0xFE):
            parts.append({0xFA: r"\l", 0xFB: r"\p", 0xFE: r"\n"}[value])
        elif value == 0xFD:
            code = rom[pos]
            parts.append(f"\\{code:02X}" if battle else FD_MACROS.get(code, f"\\{code:02X}"))
            pos += 1
        elif value == 0xFC:
            size = 1 + fc_arg_count(rom[pos])
            parts.append("\\CC" + rom[pos:pos + size].hex().upper())
            pos += size
        elif value == 0xF8:
            parts.append(f"\\btn{rom[pos]:02X}")
            pos += 1
        elif value in PCS_CHAR_TABLE:
            parts.append(PCS_CHAR_TABLE[value])
        else:
            raise ValueError(f"Unsupported PCS byte {value:02x} at {pos-1:x}")
    raise ValueError("Unterminated text")


def generate(rom):
    if hashlib.sha256(rom).hexdigest() != SHA256:
        raise ValueError("This manifest requires the unmodified HnS 2.0.5 ROM")
    entries = {}
    glossary = Glossary(source_lang="en", target_lang="it")

    def ptr(source):
        return int.from_bytes(rom[source:source + 4], "little") - 0x08000000

    def add(source, category):
        address = ptr(source)
        text, raw = decode(rom, address, battle=category == "native_battle")
        if not text:
            return
        key = (address, category)
        if key not in entries:
            entry = dict(id=f"hns_{category}_{address:x}", category=category,
                         address=hex(address), original=text, byte_length=len(raw),
                         expected_hex=raw.hex(), pointer_sources=[], is_pointer_based=True,
                         translations={})
            if category == "native_item_names":
                entry.update(max_lines=1, max_line_width=18)
                translated = glossary.lookup(text)
                if translated and len(translated) <= 18:
                    entry["translations"]["it"] = translated
            elif category == "native_item_descriptions":
                entry.update(max_lines=3, max_line_width=18)
            entries[key] = entry
        entries[key]["pointer_sources"].append(hex(source))

    for index in range(ITEM_COUNT):
        base = ITEM_BASE + index * ITEM_STRIDE
        for offset, category in ((20, "native_item_names"), (24, "native_item_names"),
                                 (12, "native_item_descriptions")):
            if ptr(base + offset) != -0x08000000:
                add(base + offset, category)
    for source in range(BATTLE_START, BATTLE_END, 4):
        add(source, "native_battle")

    # BattleStringExpandPlaceholders / BufferStringBattle in this exact build.
    # Resolve Thumb LDR-literal instructions instead of searching for arbitrary
    # byte patterns that happen to look like pointers in executable code.
    literal_sources = set()
    for instruction in range(0x8C000, 0x8E038, 2):
        opcode = int.from_bytes(rom[instruction:instruction + 2], "little")
        if opcode & 0xF800 != 0x4800:
            continue
        source = ((instruction + 4) & ~3) + (opcode & 0xFF) * 4
        address = ptr(source)
        if not 0x476500 <= address < 0x476B60 or source in literal_sources:
            continue
        try:
            text, _ = decode(rom, address, battle=True)
        except ValueError:
            continue
        if not any(c.isalpha() for c in text):
            continue
        add(source, "native_battle")
        literal_sources.add(source)
        entries[address, "native_battle"].setdefault("literal_instructions", {})[hex(source)] = hex(instruction)

    # The 40-byte expanded trainerbattle command is wholly validated, including
    # its unused fields. Never accept a single opcode/pointer coincidence.
    for start in range(0x290000, 0x400000 - 40):
        if rom[start] != 0x5C or rom[start + 1] not in (4, 6, 0x14, 0x24, 0x34):
            continue
        if rom[start + 2] != 0 or not 0 < int.from_bytes(rom[start+3:start+5], "little") < 4096:
            continue
        if rom[start+17:start+40] != bytes(23):
            continue
        script = ptr(start + 13)
        if script != -0x08000000 and not 0x290000 <= script < 0x400000:
            continue
        try:
            for offset in (5, 9):
                address = ptr(start + offset)
                if not 0x290000 <= address < 0x400000:
                    raise ValueError("Outside script text region")
                text, _ = decode(rom, address)
                if sum(c.isalpha() for c in text) < 2:
                    raise ValueError("Not dialogue")
        except ValueError:
            continue
        for offset in (5, 9):
            add(start + offset, "native_trainer")
    return dict(id="hns-2.0.5-battle-items", sha256=SHA256,
                sources=["https://github.com/PokemonHnS-Development/pokehns-expansion/blob/master/" + p
                         for p in ("include/item.h", "src/data/items.h", "src/battle_message.c", "asm/macros/event.inc")],
                entries=list(entries.values()))


if __name__ == "__main__":
    profile = generate(Path(sys.argv[1]).read_bytes())
    output = Path(__file__).resolve().parents[1] / "src/meowth/data/native_hns_2_0_5_battle_items.json"
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from collections import Counter
    print(Counter(entry["category"] for entry in profile["entries"]))
