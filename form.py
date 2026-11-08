import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

_SEP = r"[-:–—=]"
_LABELS = {
    "seller": r"seller",
    "buyer": r"buyer",
    "amount": r"amount",
    "rate": r"rate",
    "time": r"time",
    "chain": r"(?:\[\s*must\s*\]\s*)?(?:crypto\s+)?chain",
    "terms": r"(?:mention\s+)?terms",
}
_LINE_RE = {
    key: re.compile(rf"^\s*{label}\b[^\n]*?{_SEP}\s*(.*)$", re.IGNORECASE)
    for key, label in _LABELS.items()
}
_TERMS_RE = re.compile(rf"^\s*(?:mention\s+)?terms\b(?:\s*\[[^\]]*\])?[^\n]*?{_SEP}\s*(.*)$", re.IGNORECASE)
_USERNAME_RE = re.compile(r"@([A-Za-z0-9_]{4,32})")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class DealForm:
    seller: str
    buyer: str
    amount: Decimal
    rate: str
    time: str
    chain: str
    terms: str


def parse_number(text: str) -> Optional[Decimal]:
    m = _NUMBER_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        value = Decimal(m.group(0))
    except InvalidOperation:
        return None
    return value if value > 0 else None


def parse_form(text: Optional[str]) -> Optional[DealForm]:
    if not text:
        return None
    values: dict[str, str] = {}
    lines = text.splitlines()
    for i, line in enumerate(lines):
        tm = _TERMS_RE.match(line)
        if tm and "terms" not in values:
            rest = [tm.group(1).strip(), *[l.strip() for l in lines[i + 1:]]]
            values["terms"] = "\n".join(x for x in rest if x).strip()
            break
        for key, rx in _LINE_RE.items():
            if key == "terms" or key in values:
                continue
            m = rx.match(line)
            if m:
                values[key] = m.group(1).strip()
                break

    seller = _USERNAME_RE.search(values.get("seller", ""))
    buyer = _USERNAME_RE.search(values.get("buyer", ""))
    amount = parse_number(values.get("amount", ""))
    rate = parse_number(values.get("rate", ""))
    chain = values.get("chain", "").strip()
    if not (seller and buyer and amount and rate and chain):
        return None
    return DealForm(
        seller=seller.group(1),
        buyer=buyer.group(1),
        amount=amount,
        rate=values["rate"],
        time=values.get("time", ""),
        chain=chain,
        terms=values.get("terms", ""),
    )


def fmt_amount(value: Decimal) -> str:
    """100 -> 100.0, 100.50 -> 100.5, 99.25 -> 99.25"""
    text = format(value.normalize(), "f")
    return text if "." in text else f"{text}.0"
