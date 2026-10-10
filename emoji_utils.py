import re

_EMOJI_RE = re.compile(r"(<a?:\w+:\d{17,22}>)|[\U00002190-\U000027BF\U00002B00-\U00002BFF\U0001F000-\U0001FAFF]")
MAX_EMOJI_LEN = 64


def is_valid_emoji(value: str | None) -> bool:
    return bool(value) and len(value) <= MAX_EMOJI_LEN and bool(_EMOJI_RE.search(value))