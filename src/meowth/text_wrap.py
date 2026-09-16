"""Auto line-wrapping for translated GBA Pokemon text.

Inserts \\n (newline) and \\p (page break) so text fits GBA text boxes.

Design:
  - A text box shows 2 lines.
  - Each line holds ~15 CJK characters (width 2 each) or ~30 ASCII chars.
  - \\n = newline within same box, \\p = page break (wait for A, clear box).

Three-level break handling:
  - \\n\\n (literal double newline) = paragraph break → \\p (page break)
  - \\n (literal single newline) = semantic newline → forced line break
  - No newline = continuous text → auto-wrap at LINE_WIDTH
"""

import re
from .pcs_codes import CONTROL_CODE_REGEX

LINE_WIDTH = 32        # max width units per line (16 CJK chars × 2)
LINES_PER_BOX = 2      # lines per text box

# Variable width estimates
_VAR_WIDTHS = {"player": 6, "rival": 6}
_DEFAULT_VAR_WIDTH = 8

# HMA color/style bracket codes (zero display width)
_COLOR_NAMES = {
    "white", "white2", "white3", "black",
    "grey", "gray", "darkgrey", "darkgray", "lightgrey", "lightgray",
    "red", "orange", "green", "lightgreen",
    "blue", "lightblue", "lightblue2", "lightblue3",
    "cyan", "navyblue", "darknavyblue",
    "transp", "yellow", "magenta", "skyblue", "darkskyblue", "black2",
}

# CJK punctuation that must not start a line
_NO_BREAK_BEFORE = set("。，！？、）」』】〉》：；…～")

# CJK punctuation that must not end a line (next char must stay with it)
_NO_BREAK_AFTER = set("（「『【〈《")
_LATIN_CLOSING = set(".,!?;:%)]}»”’") | {r"\.", r"\qc"}

# Common compound words that should not be split across lines
_COMPOUNDS = [
    "宝可梦", "红白机", "精灵球", "训练师", "道馆主", "冠军联盟",
    "大木博士", "小智", "小茂", "火箭队", "四天王",
    "妙蛙种子", "小火龙", "杰尼龟", "皮卡丘",
]

# Tokenizer: control codes, variables, compound words, ASCII words, or single chars
_TOKEN_RE = re.compile(
    r"(?:" + CONTROL_CODE_REGEX.pattern + r")"
    r"|\\btn[0-9A-Fa-f]{2}"
    r"|\\CC[0-9A-Fa-f]{4}"
    r"|\\B[0-9A-Fa-f]"
    r"|\\\?[0-9A-Fa-f]{2}"
    r"|\\[plnr]"
    r"|\[[a-zA-Z_]\w*\]"
    r"|" + "|".join(re.escape(w) for w in sorted(_COMPOUNDS, key=len, reverse=True))
    + r"|[A-Za-zÀ-ÖØ-öø-ÿ0-9]+(?:['’\-][A-Za-zÀ-ÖØ-öø-ÿ0-9]+)*"
    r"|.",
    re.DOTALL,
)


def wrap_text(text: str, line_width: int = LINE_WIDTH,
              lines_per_box: int = LINES_PER_BOX, target_lang: str = "zh-Hans") -> str:
    """Wrap translated text to fit GBA text boxes.

    Handles three levels of breaks from the input:
    - \\n\\n (or \\p) = paragraph break → always emits \\p
    - \\n (single) = semantic newline → forced line break within text flow
    - continuous text = auto-wrapped at line_width
    """
    if not text:
        return text
    if line_width < 1 or lines_per_box < 1:
        raise ValueError("Text box dimensions must be positive")

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Step 1: Split on paragraph breaks (\\p, \\n\\n).
    _PARA = "\x00PARA\x00"
    text = text.replace("\\p", _PARA)
    text = text.replace("\n\n", _PARA)

    paragraphs = text.split(_PARA)

    # Step 2: Process each paragraph
    wrapped_paras = []
    for para in paragraphs:
        if not para.strip():
            wrapped_paras.append("")
            continue

        # Split on semantic newlines (single \n from _classify_newlines)
        segments = para.split("\n")

        # Strip HMA layout codes within each segment
        cleaned_segments = []
        for seg in segments:
            s = seg.replace("\\n", " ").replace("\\l", " ")
            if s.strip():
                cleaned_segments.append(s)

        if not cleaned_segments:
            continue

        # Wrap each segment into display lines, then distribute into boxes
        all_lines: list[str] = []
        for seg in cleaned_segments:
            seg_lines = _wrap_to_lines(seg, line_width)
            if target_lang == "it":
                seg_lines = _balance_last_line(seg_lines, line_width)
            all_lines.extend(seg_lines)

        wrapped_paras.append(_distribute_lines(all_lines, lines_per_box))

    return "\\p".join(wrapped_paras)

def _token_width(token: str) -> int:
    """Return display width of a token."""
    if token.startswith("\\btn"):
        return 2
    if token == r"\.":
        return 1
    if re.fullmatch(r"\\[0-9A-F]{2}", token):
        return _DEFAULT_VAR_WIDTH
    if token.startswith("\\"):
        return 0
    if token.startswith("[") and token.endswith("]"):
        name = token[1:-1].lower()
        if name in _COLOR_NAMES:
            return 0
        return _VAR_WIDTHS.get(name, _DEFAULT_VAR_WIDTH)
    w = 0
    for ch in token:
        if _is_wide(ch):
            w += 2
        else:
            w += 1
    return w


def _is_wide(ch: str) -> bool:
    """Return True for CJK / fullwidth characters (width 2)."""
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF
        or 0x3400 <= cp <= 0x4DBF
        or 0x3000 <= cp <= 0x303F
        or 0xFF01 <= cp <= 0xFF60
        or 0xFE30 <= cp <= 0xFE4F
    )


def _can_break_before(tokens: list[str], idx: int) -> bool:
    """Check whether we may insert a line break before tokens[idx]."""
    tok = tokens[idx]
    if tok in _NO_BREAK_BEFORE:
        return False
    if idx > 0 and tokens[idx - 1] in _NO_BREAK_AFTER:
        return False
    return True


def _wrap_to_lines(text: str, line_width: int) -> list[str]:
    """Wrap a continuous text segment into a list of display lines."""
    tokens = [match.group() for match in _TOKEN_RE.finditer(text)]
    # Keep Latin punctuation with its word before measuring the line. Merely
    # forbidding a break before it can overflow a box that is already full.
    groups: list[list[str]] = []
    for token in tokens:
        if token in _LATIN_CLOSING and groups:
            while groups and all(part.isspace() for part in groups[-1]):
                groups.pop()
            if groups:
                while len(groups) > 1 and sum(_token_width(part) for part in groups[-1]) == 0:
                    trailing_controls = groups.pop()
                    groups[-1].extend(trailing_controls)
                groups[-1].append(token)
                continue
        groups.append([token])
    if not tokens:
        return [text] if text else []

    lines: list[list[str]] = [[]]
    line_pos = 0

    tokens = ["".join(group) for group in groups]
    for i, (tok, group) in enumerate(zip(tokens, groups)):
        if tok.isspace() and line_pos == 0:
            continue
        w = sum(_token_width(part) for part in group)

        if w > 0 and line_pos > 0 and line_pos + w > line_width:
            if _can_break_before(tokens, i):
                lines.append([])
                line_pos = 0

        if tok.isspace() and line_pos == 0:
            continue

        lines[-1].append(tok)
        line_pos += w

    return ["".join(line).strip() for line in lines if line]


def _balance_last_line(lines: list[str], line_width: int) -> list[str]:
    """Avoid a lone final word when two words still fit without overflow."""
    if len(lines) < 2 or len(lines[-1].split()) != 1:
        return lines
    previous = lines[-2].rsplit(" ", 1)
    if len(previous) != 2 or len(previous[0].split()) < 2:
        return lines
    candidate = previous[1] + " " + lines[-1]
    if sum(_token_width(m.group()) for m in _TOKEN_RE.finditer(candidate)) <= line_width:
        lines[-2:] = [previous[0], candidate]
    return lines


def _distribute_lines(lines: list[str], lines_per_box: int) -> str:
    """Distribute wrapped lines into text boxes.

    Uses \\n for newlines within a box, \\p for page breaks between boxes.
    """
    if not lines:
        return ""

    parts: list[str] = []
    line_in_box = 0

    for i, line in enumerate(lines):
        if i > 0:
            if line_in_box >= lines_per_box:
                parts.append("\\p")
                line_in_box = 0
            else:
                parts.append("\\n")
        parts.append(line)
        line_in_box += 1

    return "".join(parts)
