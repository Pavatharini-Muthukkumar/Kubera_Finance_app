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


class GeminiUnavailable(RuntimeError):
    """Every model in the chain failed; retrying the same batch would only wait longer."""


def gemini_generator(model: str, timeout_s: int = 25, deadline_s: int = 60) -> Generate:
    """``model`` may list fallbacks, comma-separated: an overloaded (503), rate-limited (429)
    or retired (404) model hands over to the next one at once. Each call is capped at
    ``timeout_s`` and one prompt at ``deadline_s`` over all models, so a page never hangs."""
    from google import genai
    from google.genai import errors, types

    models = [m.strip() for m in model.split(",") if m.strip()]
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=timeout_s * 1000))
    plain = types.GenerateContentConfig(response_mime_type="application/json", temperature=0)
    fast = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0,
        thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW),  # picking a label needs little thought
    )
    no_thinking_level: set[str] = set()  # models that reject the setting

    def call(name: str, prompt: str) -> str:
        if name not in no_thinking_level:
            try:
                return client.models.generate_content(model=name, contents=prompt, config=fast).text
            except errors.ClientError as e:
                if e.code != 400 or "think" not in str(e).lower():
                    raise
                no_thinking_level.add(name)
        return client.models.generate_content(model=name, contents=prompt, config=plain).text

    def generate(prompt: str) -> str:
        start, last = time.monotonic(), None
        for i, name in enumerate(list(models)):
            if time.monotonic() - start > deadline_s:
                break
            try:
                text = call(name, prompt)
                if i:  # the one that answered goes first next time
                    models.insert(0, models.pop(i))
                return text
            except Exception as e:  # overloaded, rate-limited, retired, timed out
                if isinstance(e, errors.ClientError) and e.code not in (404, 429):
                    raise GeminiUnavailable(str(e)) from e  # bad key or request: no model will do better
                log.warning("Gemini model %s failed (%s), trying the next one", name, str(e)[:120])
                last = e
        raise GeminiUnavailable(f"no Gemini model answered: {last}") from last

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


def _ask(
    generate: Generate, texts: list[str], retries: int = 3, rejected: list[str] | None = None
) -> dict[int, tuple[str, str]]:
    """One batch. Raises the last error when every retry failed; off-table answers count as rejected."""
    prompt = PROMPT.format(
        categories=_category_table(),
        items="\n".join(f"{i}: {t[:300]}" for i, t in enumerate(texts)),
    )
    for attempt in range(retries):
        try:
            answer = json.loads(generate(prompt))
            if isinstance(answer, dict):  # some replies wrap the list: {"transactions": [...]}
                answer = next((v for v in answer.values() if isinstance(v, list)), [])
            out = {}
            for item in answer:
                i = int(item.get("id", -1))
                main, sub = str(item.get("main_category", "")), str(item.get("subcategory", ""))
                if not 0 <= i < len(texts):
                    continue
                if main and not is_valid_category(main, sub):
                    log.warning("Gemini answered %r -> %r for %r: not in the category table", main, sub, texts[i])
                    if rejected is not None:
                        rejected.append(f"{texts[i]}: {main} -> {sub}")
                    main, sub = "", ""
                out[i] = (main, sub)
            return out
        except GeminiUnavailable:
            raise  # the model chain already tried everything
        except Exception as e:  # malformed JSON, flaky network
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt * 2
            log.warning("Gemini batch failed (%s); retry %d/%d in %ds", e, attempt + 1, retries, wait)
            time.sleep(wait)
    return {}


def _ask_all(
    generate: Generate,
    texts: list[str],
    batch_size: int,
    errors: list[str] | None = None,
    rejected: list[str] | None = None,
) -> tuple[dict[str, tuple[str, str]], int]:
    answers, calls = {}, 0
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        calls += 1
        try:
            pairs = _ask(generate, batch, rejected=rejected)
        except Exception as e:
            log.error("Gemini batch of %d texts failed: %s", len(batch), e)
            if errors is not None:
                errors.append(f"Gemini call failed: {type(e).__name__}: {str(e)[:200]}")
            continue
        for i, pair in pairs.items():
            answers[batch[i]] = pair
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
    calls, errors, rejected = 0, [], []
    if generate is not None and texts:
        answers, calls = _ask_all(generate, texts, config.gemini_batch_size, errors, rejected)
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
    df = mark_excluded(df)
    df.attrs["gemini_errors"] = errors
    df.attrs["gemini_rejected"] = rejected
    return df


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
