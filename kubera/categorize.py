"""Gemini categorisation: batched, cached, validated against the category table."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from kubera.config import Config
from kubera.schema import CATEGORIES, is_valid_category
from kubera.transform import mark_excluded

log = logging.getLogger(__name__)

# (prompt) -> raw JSON text. Injected so tests and --no-llm runs need no API key.
Generate = Callable[[str], str]

PROMPT = """You categorise German bank transactions for a personal finance app.

Allowed categories (Main Category -> Subcategories). Use ONLY these exact pairs:
{categories}

Rules:
- Judge the merchant, the purpose text and any recognisable pattern together.
- Drugstores (dm, Rossmann, Müller) are Groceries -> Drugstore unless the text clearly says otherwise.
- Paying off a credit card is Banking -> Credit Card Statement.
- If nothing fits clearly, answer "" for both fields. Never invent a category.

Transactions (id: text):
{items}

Answer with a JSON array, one object per id:
[{{"id": <id>, "main_category": "...", "subcategory": "..."}}]
"""


def _category_table() -> str:
    return "\n".join(f"- {main} -> {', '.join(subs)}" for main, subs in CATEGORIES.items())


def gemini_generator(model: str) -> Generate:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0)

    def generate(prompt: str) -> str:
        return client.models.generate_content(model=model, contents=prompt, config=cfg).text

    return generate


class CategoryCache:
    """text -> [main, sub], persisted so a re-run never pays for the same text twice."""

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, list[str]] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("cache %s is unreadable, starting empty", path)

    @staticmethod
    def key(text: str) -> str:
        return " ".join(text.lower().split())

    def get(self, text: str):
        return self.data.get(self.key(text))

    def put(self, text: str, main: str, sub: str) -> None:
        self.data[self.key(text)] = [main, sub]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)  # atomic: a crash never leaves a half-written cache


def _ask(generate: Generate, texts: list[str], retries: int = 3) -> dict[int, tuple[str, str]]:
    prompt = PROMPT.format(
        categories=_category_table(),
        items="\n".join(f"{i}: {t[:300]}" for i, t in enumerate(texts)),
    )
    for attempt in range(retries):
        try:
            answer = json.loads(generate(prompt))
            out = {}
            for item in answer:
                i = int(item.get("id", -1))
                main, sub = str(item.get("main_category", "")), str(item.get("subcategory", ""))
                if 0 <= i < len(texts):
                    out[i] = (main, sub) if is_valid_category(main, sub) else ("", "")
            return out
        except Exception as e:  # network, quota, malformed JSON
            wait = 2 ** attempt * 5
            log.warning("Gemini batch failed (%s); retry %d/%d in %ds", e, attempt + 1, retries, wait)
            time.sleep(wait)
    return {}


def categorize(df: pd.DataFrame, config: Config, generate: Generate | None = None) -> pd.DataFrame:
    """Fill Main Category / Subcategory for rows the rules left empty.

    Each distinct text is asked once (cache + de-duplication), in batches.
    With ``generate=None`` only the cache is used -- the --no-llm mode.
    """
    cache = CategoryCache(config.state_dir / "category_cache.json")
    todo = df["Main Category"].fillna("") == ""
    texts = sorted({t for t in df.loc[todo, "text"] if t and cache.get(t) is None})

    asked = 0
    if generate is not None:
        size = config.gemini_batch_size
        for start in range(0, len(texts), size):
            batch = texts[start : start + size]
            answers = _ask(generate, batch)
            for i, text in enumerate(batch):
                if i in answers:  # unanswered texts stay uncached and are retried next run
                    cache.put(text, *answers[i])
            asked += 1
        cache.save()

    hits = 0
    for idx in df.index[todo]:
        cached = cache.get(df.at[idx, "text"] or "")
        if cached:
            df.loc[idx, ["Main Category", "Subcategory"]] = cached
            hits += 1
    log.info("categorised %d rows (%d model calls for %d new texts)", hits, asked, len(texts))
    return mark_excluded(df)
