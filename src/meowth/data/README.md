The bundled English/Italian terminology is derived from the PokeAPI CSV data:
https://github.com/PokeAPI/pokeapi/tree/master/data/v2/csv

Refresh it with `python scripts/update_italian_glossary.py` after installing the
project. Species names are preserved verbatim by the Italian translation pipeline;
the other categories provide localized names for tables and proper-noun context.

`moves_it.json` keeps moves separate to avoid collisions such as the Psychic move
and Psychic type. It contains Italian names and source spellings from the six
supported languages. Refresh it with `python scripts/update_move_glossary.py`.
English Gen III aliases come from the pret/pokeemerald name and constant tables;
their source URLs are recorded in the JSON. IDs link the source datasets only:
ROM table indices are never used to guess a move's identity in a hack.

When a name does not fit a fixed GBA slot, `move_glossary.py` uses historical
Italian spellings documented in these references:

- https://wiki.pokemoncentral.it/Elenco_delle_mosse_modificate#Cambiamento_di_nome
- https://wiki.pokemoncentral.it/Destinobbl.

Unknown moves and names that cannot fit even with a documented short spelling
remain unchanged, with a warning. No arbitrary truncation is used for move names.
