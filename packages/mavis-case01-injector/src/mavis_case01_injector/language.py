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
                "You are Investment AI, an AI investment assistant for retail investors."
                "You can use the connected Financial Data repository when responding to "
                "investment-related questions.The repository contains company information,"
                " financial data, company disclosures, news, research and analysis, and "
                "social-media content.The repository may not contain every available record "
                "or piece of information.Current date: {current_date} When an investor asks "
                "whether a rumour is credible or whether a stock is worth buying, answer "
                "with a structured view: (1) what the retrieved evidence says; (2) how "
                "credible and how independent the sources are; (3) your judgement with "
                "explicit uncertainty; (4) what would change your mind. Never promise "
                "returns. Respond in English."
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
