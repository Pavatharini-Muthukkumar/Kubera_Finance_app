"""Hybrid categorisation: merchant rules, then Gemini (batched, cached, validated)."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

import pandas as pd

from kubera import rules
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


def _ask_all(generate: Generate, texts: list[str], batch_size: int) -> tuple[dict[str, tuple[str, str]], int]:
    answers, calls = {}, 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        for i, pair in _ask(generate, batch).items():
            answers[batch[i]] = pair
        calls += 1
    return answers, calls


def categorize(
    df: pd.DataFrame,
    config: Config,
    generate: Generate | None = None,
    max_new_texts: int | None = None,
) -> pd.DataFrame:
    """Fill Main Category / Subcategory for every row the own-account rule left empty.

    Hybrid, and every row records which path decided it (``Categorised By``):
      1. rule   -- a short list of unmistakable merchants (kubera/rules.py)
      2. gemini -- everything else; each distinct text is asked once, in
                   batches, and remembered in the cache so a re-run is free
    With ``generate=None`` only rules and earlier Gemini answers are used.
    ``max_new_texts`` caps the Gemini calls of one run (the public demo).
    """
    df = df.copy()
    if "Categorised By" not in df:
        df["Categorised By"] = ""

    def open_rows():
        return df.index[df["Main Category"].fillna("") == ""]

    for idx in open_rows():
        hit = rules.match(df.at[idx, "text"])
        if hit:
            df.loc[idx, ["Main Category", "Subcategory", "Categorised By"]] = [*hit, "rule"]

    cache = CategoryCache(config.state_dir / "category_cache.json")
    texts = sorted({t for t in df.loc[open_rows(), "text"] if t and cache.get(t) is None})
    if max_new_texts is not None:
        texts = texts[:max_new_texts]
    calls = 0
    if generate is not None and texts:
        answers, calls = _ask_all(generate, texts, config.gemini_batch_size)
        for text, pair in answers.items():  # unanswered texts stay uncached and are retried next run
            cache.put(text, *pair)
        cache.save()

    for idx in open_rows():
        cached = cache.get(df.at[idx, "text"] or "")
        if cached:
            source = "gemini" if any(cached) else ""  # Gemini looked and found no fitting category
            df.loc[idx, ["Main Category", "Subcategory", "Categorised By"]] = [*cached, source]

    counts = df["Categorised By"].replace("", "needs review").value_counts().to_dict()
    log.info("categorised: %s (%d Gemini calls for %d new texts)", counts, calls, len(texts))
    return mark_excluded(df)


def rule_agreement(df: pd.DataFrame, config: Config, generate: Generate) -> pd.DataFrame:
    """Ask Gemini about every rule-decided text and compare: the rules' audit.

    Returns one row per distinct text with both answers and an ``agree`` flag,
    so the rules can be defended with a number ("rules and Gemini agree on 97%").
    """
    ruled = df[df["Categorised By"] == "rule"]
    texts = sorted(set(ruled["text"]))
    answers, _ = _ask_all(generate, texts, config.gemini_batch_size)
    rows = []
    for text in texts:
        rule = rules.match(text)
        model = answers.get(text)
        rows.append({
            "text": text,
            "rule": " → ".join(rule),
            "gemini": " → ".join(model) if model and any(model) else "(no answer)",
            "agree": model == rule,
        })
    return pd.DataFrame(rows, columns=["text", "rule", "gemini", "agree"])
