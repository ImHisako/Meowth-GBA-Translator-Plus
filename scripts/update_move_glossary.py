"""Build a category-specific Italian move dictionary, including Gen III aliases."""

import csv
import io
import json
import re
from pathlib import Path
from urllib.request import urlopen

SOURCES = {
    "names": "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/move_names.csv",
    "gba_names": "https://raw.githubusercontent.com/pret/pokeemerald/master/src/data/text/move_names.h",
    "gba_ids": "https://raw.githubusercontent.com/pret/pokeemerald/master/include/constants/moves.h",
}


def main():
    documents = {}
    for key, url in SOURCES.items():
        with urlopen(url, timeout=60) as response:
            documents[key] = response.read().decode("utf-8")
    languages = {9: "en", 8: "it", 5: "fr", 6: "de", 7: "es", 12: "zh-Hans"}
    moves = {}
    for row in csv.DictReader(io.StringIO(documents["names"])):
        language = languages.get(int(row["local_language_id"]))
        if language:
            move_id = int(row["move_id"])
            moves.setdefault(move_id, {"id": move_id, "names": {}})["names"][language] = row["name"]
    constants = {name: int(number) for name, number in re.findall(
        r"^#define\s+(MOVE_\w+)\s+(\d+)\s*$", documents["gba_ids"], re.MULTILINE
    )}
    for name, spelling in re.findall(r'\[(MOVE_\w+)\]\s*=\s*_\("([^"]+)"\)', documents["gba_names"]):
        move_id = constants[name]
        if move_id:
            moves[move_id]["gba_en"] = spelling
    result = [move for move in moves.values() if "it" in move["names"]]
    assert all("gba_en" in moves[number] for number in range(1, 355))
    destination = Path(__file__).resolve().parents[1] / "src/meowth/data/moves_it.json"
    destination.write_text(json.dumps({"sources": SOURCES, "moves": result}, ensure_ascii=False, indent=2)
                           + "\n", encoding="utf-8", newline="\n")
    print(f"Saved {len(result)} moves, including all 354 Gen III spellings")


if __name__ == "__main__":
    main()
