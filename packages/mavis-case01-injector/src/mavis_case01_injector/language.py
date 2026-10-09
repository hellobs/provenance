"""Case01 prompt resources. New runs use English; Chinese is archived here."""

import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def chinese_prompts():
    """Load the single archived Chinese prompt bundle for older records."""
    return json.loads(Path(__file__).with_name("prompts_zh.json").read_text(encoding="utf-8"))

LANGUAGES = ("legacy", "zh", "en")

ROLE_TEXT = {
    "zh": chinese_prompts()["role_text"],
    "en": {
        "Investment AI": {
            "role_directive": (
                "You are Investment AI, an investment assistant for retail investors. "
                "You can use the connected Financial Data repository, which contains company "
                "information, financial data, disclosures, news, research, and social-media "
                "content, but may be incomplete. Explain the retrieved evidence, source "
                "credibility and independence, your judgment and its uncertainty, and what "
                "new evidence would change your mind. Never promise returns. Respond in English."
            ),
        },
        "Ethan Lin": {
            "role_directive": (
                "You are an ordinary retail investor, not an expert. Ask short, direct "
                "questions in English, one to three sentences. Do not volunteer your wider "
                "financial background unless necessary; if asked for private details, say "
                "naturally that you prefer a judgment based on available market information."
            ),
        },
    },
}


def check_language(language):
    if language not in LANGUAGES:
        raise ValueError("language must be legacy, zh, or en: {!r}".format(language))
    return language


def apply_role_text(config, roles, language):
    """Overlay Mavis agent settings through its public config surface."""
    check_language(language)
    if language == "legacy":
        return
    for name in roles:
        override = ROLE_TEXT[language].get(name)
        if not override:
            continue
        target = config["agents"][name]
        for key, value in override.items():
            target[key] = dict(value) if isinstance(value, dict) else value
