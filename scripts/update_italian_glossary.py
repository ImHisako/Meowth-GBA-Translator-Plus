"""Refresh bundled English/Italian terminology from PokeAPI's CSV data."""

import csv
import io
import json
from pathlib import Path
from urllib.request import urlopen


def main():
    from meowth.glossary import TERM_FILES

    terms, categories = {}, {}
    for category, (filename, id_column) in TERM_FILES.items():
        url = f"https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/{filename}"
        with urlopen(url, timeout=60) as response:
            rows = csv.DictReader(io.StringIO(response.read().decode("utf-8")))
            entities = {}
            for row in rows:
                language = int(row["local_language_id"])
                if language in (8, 9):
                    entities.setdefault(row[id_column], {})[language] = row["name"]
        for names in entities.values():
            if 8 in names and 9 in names:
                terms[names[9]] = names[8]
                categories[names[9]] = category
    destination = Path(__file__).resolve().parents[1] / "src/meowth/data/glossary_en_it.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({
        "source": "https://github.com/PokeAPI/pokeapi/tree/master/data/v2/csv",
        "source_to_target": terms, "term_categories": categories,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(terms)} terms to {destination}")


if __name__ == "__main__":
    main()
