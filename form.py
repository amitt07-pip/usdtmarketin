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


_STAT_NUM = r"\$?\s*([\d,]+(?:\.\d+)?)"
_STAT_SEP = r"\s*[:\-–—=]\s*"
_STAT_RES = {
    "total": re.compile(rf"total\s*escrows?{_STAT_SEP}([\d,]+)", re.IGNORECASE),
    "rank": re.compile(rf"rank(?:ing)?{_STAT_SEP}#?\s*([\d,]+)", re.IGNORECASE),
    "worth": re.compile(rf"total\s*worth{_STAT_SEP}{_STAT_NUM}", re.IGNORECASE),
    "avg": re.compile(rf"average\s*time{_STAT_SEP}(.+)$", re.IGNORECASE),
    "max": re.compile(rf"max(?:imum)?\s*escrow\s*amount{_STAT_SEP}{_STAT_NUM}", re.IGNORECASE),
    "rate": re.compile(rf"completion\s*rate{_STAT_SEP}(\d+(?:\.\d+)?)", re.IGNORECASE),
}
_DURATION_PART = re.compile(r"(\d+)\s*(d|h|m|s)", re.IGNORECASE)
_DURATION_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}

STAT_LABELS = {
    "total": "Total Escrows", "rank": "Ranking", "worth": "Total Worth",
    "avg": "Average Time", "max": "Max Escrow Amount", "rate": "Completion Rate",
}


def parse_duration(text: str) -> Optional[int]:
    """'5m' -> 300, '2h 5m' -> 7500; raises ValueError if unparseable. 'N/A' -> None."""
    text = text.strip().strip("`'\"")
    if text.upper().replace(" ", "") in ("N/A", "NA", "-"):
        return None
    parts = _DURATION_PART.findall(text)
    if not parts or _DURATION_PART.sub("", text).strip():
        raise ValueError(text)
    return sum(int(n) * _DURATION_SECONDS[u.lower()] for n, u in parts)


def parse_stats(text: Optional[str]) -> tuple[dict, list[str]]:
    """Returns (values, missing/invalid labels)."""
    found: dict[str, str] = {}
    for line in (text or "").splitlines():
        for key, rx in _STAT_RES.items():
            if key not in found:
                m = rx.search(line)
                if m:
                    found[key] = m.group(1).strip()
    values: dict = {}
    bad: list[str] = []
    for key, label in STAT_LABELS.items():
        raw = found.get(key)
        if raw is None:
            bad.append(label)
            continue
        try:
            if key in ("total", "rank"):
                values[key] = int(raw.replace(",", ""))
            elif key in ("worth", "max"):
                values[key] = Decimal(raw.replace(",", ""))
            elif key == "rate":
                values[key] = max(0, min(100, round(float(raw))))
            else:
                values[key] = parse_duration(raw)
        except (ValueError, InvalidOperation):
            bad.append(label)
    if "rank" in values and values["rank"] < 1:
        bad.append(STAT_LABELS["rank"])
    return values, bad
