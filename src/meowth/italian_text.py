"""Conservative Italian corrections around the invariant noun Pokémon."""

import re


def normalize_italian(text: str, target_lang: str) -> str:
    """Fix unambiguous article errors, leaving control codes untouched.

    An elided singular article becomes singular (del Pokémon). Plural articles
    remain plural (degli Pokémon -> dei Pokémon); number cannot safely be
    inferred from the invariant noun alone.
    """
    if target_lang.lower().split("-")[0] != "it":
        return text
    text = re.sub(r"\bpok[eéè]mon\b", "Pokémon", text, flags=re.IGNORECASE)
    plural = {"gli": "i", "degli": "dei", "agli": "ai", "negli": "nei",
              "sugli": "sui", "dagli": "dai", "quegli": "quei"}
    singular = {"dell": "del", "all": "al", "nell": "nel", "sull": "sul",
                "dall": "dal", "quell": "quel", "l": "il"}

    def case_like(value: str, source: str) -> str:
        return value.capitalize() if source[0].isupper() else value

    # Retain spacing and explicit line/page breaks. Never consume placeholders.
    gap = r"((?:\s|\\[nlp])+)"
    text = re.sub(
        r"\b(" + "|".join(plural) + r")" + gap + r"(?=Pokémon\b)",
        lambda m: case_like(plural[m[1].lower()], m[1]) + m[2],
        text, flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\b(" + "|".join(singular) + r")[’']((?:\s|\\[nlp])*)(?=Pokémon\b)",
        lambda m: case_like(singular[m[1].lower()], m[1]) + (m[2] or " "),
        text, flags=re.IGNORECASE,
    )
    # Also repair already decoded ROM text with a missing apostrophe/space.
    text = re.sub(
        r"\b(dell|all|nell|sull|dall|quell)(?=Pok[eéè]mon\b)",
        lambda m: case_like(singular[m[1].lower()], m[1]) + " ",
        text, flags=re.IGNORECASE,
    )
    return re.sub(r"\bpok[eéè]mon\b", "Pokémon", text, flags=re.IGNORECASE)
