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
    for profile in _profiles():
        if digest != profile["sha256"]:
            continue
        entries = deepcopy(profile["entries"])
        for entry in entries:
            entry["native_profile"] = profile["id"]
        return entries
    return []


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
    if entry.get("category") != "native_ui":
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
    address = int(expected["address"], 16)
    raw = bytes.fromhex(expected["expected_hex"])
    return (rom[address:address + len(raw)] == raw
            and native_layout_ok(expected, entry.get("translated", "")))
