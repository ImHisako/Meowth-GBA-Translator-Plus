"""Validation shared by providers, cache reads and the ROM pipeline."""

from collections import Counter
import re

from .pcs_codes import CONTROL_CODE_REGEX

TOKEN_RE = re.compile(r"\{[CPM]\d+\}")


class TranslationValidationError(ValueError):
    """A response cannot safely replace the source text."""


def validate_translation(source: str, translated: str) -> None:
    if not isinstance(translated, str) or (source.strip() and not translated.strip()):
        raise TranslationValidationError("Empty or invalid translation")
    if source.count("|||") != translated.count("|||"):
        raise TranslationValidationError("Unexpected batch delimiter in translation")
    if re.findall(r"\{C\d+\}", source) != re.findall(r"\{C\d+\}", translated):
        raise TranslationValidationError("Control placeholders changed or reordered")
    if Counter(re.findall(r"\{P\d+\}", source)) != Counter(re.findall(r"\{P\d+\}", translated)):
        raise TranslationValidationError("Pokemon placeholders changed")
    if Counter(re.findall(r"\{M\d+\}", source)) != Counter(re.findall(r"\{M\d+\}", translated)):
        raise TranslationValidationError("Move placeholders changed")
    if [m.group() for m in CONTROL_CODE_REGEX.finditer(source)] != [
        m.group() for m in CONTROL_CODE_REGEX.finditer(translated)
    ]:
        raise TranslationValidationError("Translation introduced or modified raw control codes")
