"""Version-specific native C text support, never a heuristic pointer scan."""

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re


@lru_cache(maxsize=1)
def _profiles() -> tuple[dict, ...]:
    folder = Path(__file__).parent / "data"
    return tuple(json.loads(path.read_text(encoding="utf-8"))
                 for path in sorted(folder.glob("native_*.json")))


def native_entries(rom: bytes | bytearray) -> list[dict]:
    """Only an exact original-ROM fingerprint enables native references."""
    digest = hashlib.sha256(rom).hexdigest()
    result = []
    for profile in _profiles():
        if digest != profile["sha256"]:
            continue
        entries = deepcopy(profile["entries"])
        for entry in entries:
            entry["native_profile"] = profile["id"]
        result.extend(entries)
    return result


def native_translation(entry: dict, target_lang: str) -> str | None:
    """Use reviewed translations from the packaged profile, not imported JSON."""
    for profile in _profiles():
        if profile["id"] == entry.get("native_profile"):
            for expected in profile["entries"]:
                if expected["id"] == entry.get("id") and expected["original"] == entry.get("original"):
                    return expected.get("translations", {}).get(target_lang)
    return None


def native_layout_ok(entry: dict, text: str) -> bool:
    """Static C menus cannot display message-box page/scroll commands."""
    category = entry.get("category")
    if category in ("native_battle", "native_trainer"):
        from .control_codes import protect
        # Layout can be reflowed, but dynamic variables, pauses, sounds and
        # explicit page boundaries must survive in their original order.
        def codes(value):
            return [code for _, code in protect(value, reflow=True)[1]
                    if category == "native_battle" or code not in (r"\p", "\n\n")]
        return codes(text) == codes(entry["original"])
    if category not in ("native_ui", "native_item_names", "native_item_descriptions"):
        return True
    buttons = re.compile(r"\\btn[0-9A-Fa-f]{2}")
    if buttons.findall(text) != buttons.findall(entry["original"]):
        return False
    text = buttons.sub("", text)
    lines = text.replace("\n", r"\n").split(r"\n")
    if len(lines) > entry["max_lines"]:
        return False
    # Profiles currently use plain menu text. Reject arbitrary controls instead
    # of inserting pauses or truncating a setting's meaning to make it fit.
    return all(not re.search(r"[\\\[\]{}]", line)
               and len(line) <= entry["max_line_width"] for line in lines)


def valid_native_entry(rom, entry: dict, expected: dict, sources: list) -> bool:
    """Validate all references and the original text before changing anything."""
    if any(entry.get(key) != expected.get(key) for key in
           ("id", "category", "native_profile", "address", "original", "byte_length")):
        return False
    if sources != expected["pointer_sources"] or entry.get("is_pointer_based") != expected["is_pointer_based"]:
        return False
    for source, instruction in expected.get("literal_instructions", {}).items():
        source, instruction = int(source, 16), int(instruction, 16)
        opcode = int.from_bytes(rom[instruction:instruction + 2], "little")
        if opcode & 0xF800 != 0x4800 or ((instruction + 4) & ~3) + (opcode & 0xFF) * 4 != source:
            return False
    address = int(expected["address"], 16)
    raw = bytes.fromhex(expected["expected_hex"])
    return (rom[address:address + len(raw)] == raw
            and native_layout_ok(expected, entry.get("translated", "")))
