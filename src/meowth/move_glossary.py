"""Official Italian move names, isolated from types/items with overlapping names."""

import json
import re
from pathlib import Path


# Historical Italian spellings for the small Gen III name slots. Used only when
# the full name does not fit; references are documented in data/README.md.
GBA_SHORT_NAMES = {
    "Wing Attack": "Att. d'Ala", "Submission": "Sottomiss.",
    "Seismic Toss": "Mov. Sismico", "Mega Drain": "Megassorbim.",
    "Quick Attack": "Att. Rapido", "Teleport": "Teletraspor.",
    "Night Shade": "Ombra Nott.", "Self-Destruct": "Autodistruz.",
    "Glare": "Bagliore", "Barrage": "Att. Pioggia", "Transform": "Trasformaz.",
    "Destiny Bond": "Destinobbl.", "Giga Drain": "Gigassorbim.",
}


def _key(text: str) -> str:
    return re.sub(r"[\s\-]+", "", text.replace("’", "'")).casefold()


class MoveGlossary:
    def __init__(self, source_lang: str = "en", target_lang: str = "it"):
        self.source_lang = source_lang
        self._index = {}
        self._rendered_names = {}
        self._pattern = None
        if target_lang != "it":
            return
        path = Path(__file__).parent / "data/moves_it.json"
        records = json.loads(path.read_text(encoding="utf-8"))["moves"]
        aliases = set()
        for record in records:
            source = record["names"].get(source_lang)
            if not source:
                continue
            spellings = {source}
            if source_lang == "en" and record.get("gba_en"):
                spellings.add(record["gba_en"])
            for spelling in spellings:
                self._index[_key(spelling)] = record
                aliases.add(spelling)
        if aliases:
            alternatives = [r"[\s\-]*".join(re.escape(part) for part in re.split(r"[\s\-]+", name))
                            for name in sorted(aliases, key=len, reverse=True)]
            self._pattern = re.compile(r"(?<!\w)(?:" + "|".join(alternatives) + r")(?!\w)", re.IGNORECASE)

    def lookup(self, source: str) -> str | None:
        record = self._index.get(_key(source))
        return record["names"]["it"] if record else None

    def fit_name(self, source: str, charmap, max_bytes: int | None = None) -> str | None:
        """Choose an attested name, never an arbitrary substring of a longer name."""
        record = self._index.get(_key(source))
        if record is None:
            return None
        candidates = [record["names"]["it"]]
        short = GBA_SHORT_NAMES.get(record["names"].get("en"))
        if short:
            candidates.append(short)
        for name in candidates:
            encoded = charmap.encode(name)
            if max_bytes is None or len(encoded) <= max_bytes:
                return name
        return None

    def set_rendered_name(self, source: str, translated: str):
        record = self._index.get(_key(source))
        if record:
            self._rendered_names[record["id"]] = translated

    def protect(self, text: str) -> tuple[str, list[tuple[str, str]]]:
        """Protect explicit English move references, without replacing everyday verbs.

        Other source languages still have exact dictionary lookup for move tables.
        Ambiguous dialogue is deliberately left to the translation provider.
        """
        if self._pattern is None or self.source_lang != "en":
            return text, []
        replacements = []
        last_reference_end = None

        def replace(match):
            nonlocal last_reference_end
            before, after = text[:match.start()], text[match.end():]
            explicit = (
                text.strip() == match.group()
                or re.search(r"\b(?:move|moves|use|used|uses|using|learn|learns|learned|learnt|learning|"
                             r"teach|teaches|taught|knows|forgot|forget|contains)\s+"
                             r"(?:(?:the|a)\s+(?:move\s+)?)?$", before, re.IGNORECASE)
                or re.match(r"\s+is\s+(?:a|an|the)\s+(?:\w+\s+){0,3}(?:move|attack)\b", after, re.IGNORECASE)
                or (last_reference_end is not None and re.fullmatch(
                    r"\s*(?:,\s*(?:(?:and|or)\s+)?|(?:and|or)\s+)",
                    text[last_reference_end:match.start()], re.IGNORECASE))
            )
            if not explicit:
                return match.group()
            record = self._index[_key(match.group())]
            last_reference_end = match.end()
            translated = self._rendered_names.get(record["id"], record["names"]["it"])
            placeholder = f"{{M{len(replacements)}}}"
            replacements.append((placeholder, translated))
            return placeholder

        return self._pattern.sub(replace, text), replacements
