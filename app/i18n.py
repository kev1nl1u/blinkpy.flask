"""JSON-backed i18n with in-memory cache. Supports: en, it, zh."""

import json
from pathlib import Path
from typing import Any, Dict, Optional

SUPPORTED_LANGUAGES = ['en', 'it', 'zh']
DEFAULT_LANGUAGE = 'en'

_translations_cache: Dict[str, Dict[str, Any]] = {}


def _load_translations(language: str) -> Dict[str, Any]:
    if language in _translations_cache:
        return _translations_cache[language]

    locales_dir = Path(__file__).parent / 'locales'
    translation_file = locales_dir / f'{language}.json'

    if not translation_file.exists():
        translation_file = locales_dir / f'{DEFAULT_LANGUAGE}.json'

    try:
        with open(translation_file, 'r', encoding='utf-8') as f:
            translations = json.load(f)
        _translations_cache[language] = translations
        return translations
    except Exception as e:
        print(f"Failed to load translations for '{language}': {e}")
        return {}


def get_translation(key: str, language: Optional[str] = None) -> str:
    """Return the translation for a dot-separated key (e.g. 'dashboard.today').
    Falls back to the key itself if not found."""
    if language is None:
        language = DEFAULT_LANGUAGE
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE

    value = _load_translations(language)
    for k in key.split('.'):
        if isinstance(value, dict):
            value = value.get(k)
        else:
            return key
    return str(value) if value is not None else key


def get_all_translations(language: Optional[str] = None) -> Dict[str, Any]:
    if language is None:
        language = DEFAULT_LANGUAGE
    if language not in SUPPORTED_LANGUAGES:
        language = DEFAULT_LANGUAGE
    return _load_translations(language)


def is_supported_language(language: str) -> bool:
    return language in SUPPORTED_LANGUAGES


def get_supported_languages() -> list:
    return SUPPORTED_LANGUAGES.copy()
