"""Keyword rules for well-known German merchants.

Applied before the model: a booking at REWE is groceries without asking
anyone, which saves calls and makes the demo work without an API key.
Rules only ever produce pairs from the category table.
"""

from __future__ import annotations

import re

from kubera.schema import is_valid_category

# (regex on the cleaned booking text, main category, subcategory). First match wins.
RULES: list[tuple[str, str, str]] = [
    (r"\b(rewe|edeka|aldi|lidl|netto|penny|kaufland|norma|tegut|globus)\b", "Groceries", "Supermarket"),
    (r"\b(dm-drogerie|dm drogerie|rossmann|müller drogerie)\b|^dm\b", "Groceries", "Drugstore"),
    (r"\b(asia markt|indian store|türkischer markt)\b", "Groceries", "International Grocery"),
    (r"\b(lieferando|wolt|uber eats|deliveroo)\b", "Dining Out", "Delivery"),
    (r"\b(mcdonald|burger king|kfc|subway|domino)\b", "Dining Out", "Fast Food"),
    (r"\b(starbucks|cafe|café|coffee|bäckerei|backerei)\b", "Dining Out", "Cafe"),
    (r"\b(restaurant|ristorante|pizzeria|trattoria)\b", "Dining Out", "Restaurant"),
    (r"\b(aral|shell|esso|jet tankstelle|total ?energies|tankstelle)\b", "Car", "Fuel"),
    (r"\b(parkhaus|parking|apcoa|easypark)\b", "Car", "Parking"),
    (r"\b(apotheke|pharmacy)\b", "Health", "Pharmacy"),
    (r"\b(techniker krankenkasse|tk |aok|barmer|dak)\b", "Health", "Health Insurance"),
    (r"\b(miete|rent|hausverwaltung)\b", "Housing", "Rent"),
    (r"\b(stadtwerke|e\.on|eon |vattenfall|enbw|strom)\b", "Housing", "Electricity"),
    (r"\b(telekom|vodafone|o2|1&1|congstar)\b", "Housing", "Internet & Phone"),
    (r"\b(rundfunk|ard zdf|beitragsservice)\b", "Housing", "Broadcast Fee (GEZ)"),
    (r"\b(ikea|poco|xxxlutz|höffner)\b", "Housing", "Furniture"),
    (r"\b(trade republic|scalable|comdirect depot|etf|sparplan)\b", "Savings", "Investments"),
    (r"\b(zalando|h&m|c&a|primark|zara|about you)\b", "Shopping", "Clothing"),
    (r"\b(mediamarkt|media markt|saturn|apple\.com|cyberport)\b", "Shopping", "Electronics"),
    (r"\b(amazon|amzn|ebay|otto|temu|aliexpress)\b", "Shopping", "Online Shopping"),
    (r"\b(netflix|spotify|disney|prime video|youtube premium|dazn|audible)\b", "Leisure", "Subscription"),
    (r"\b(kino|cinema|cinemaxx|uci)\b", "Leisure", "Cinema"),
    (r"\b(lufthansa|ryanair|eurowings|booking\.com|airbnb|hotel)\b", "Leisure", "Travel"),
    (r"\b(steam|playstation|nintendo|xbox)\b", "Leisure", "Games"),
    (r"\b(mcfit|fitx|urban sports|gym)\b", "Lifestyle", "Gym Membership"),
    (r"\b(friseur|hairdresser|barber)\b", "Lifestyle", "Hairdresser"),
    (r"\b(deutsche bahn|db vertrieb|bahn\.de|mvg|bvg|vgn|hvv|deutschlandticket)\b", "Mobility", "Public Transport"),
    (r"\b(flixbus|uber|bolt|freenow|free now|taxi)\b", "Mobility", "Taxi"),
    (r"\b(tier|lime|voi|share now|miles)\b", "Mobility", "Shared Mobility"),
    (r"\b(gehalt|lohn|salary)\b", "Income", "Salary"),
    (r"\b(kindergeld|familienkasse)\b", "Income", "Child Benefit"),
    (r"\b(finanzamt)\b", "Government", "Taxes"),
    (r"\b(kontoführung|kontofuehrung|entgelt|gebühr)\b", "Banking", "Bank Fees"),
]

_COMPILED = [(re.compile(p, re.I), m, s) for p, m, s in RULES]
assert all(is_valid_category(m, s) for _, m, s in RULES), "rule outside the category table"


def match(text: str) -> tuple[str, str] | None:
    for pattern, main, sub in _COMPILED:
        if pattern.search(text or ""):
            return main, sub
    return None
