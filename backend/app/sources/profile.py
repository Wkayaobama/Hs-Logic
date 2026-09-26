"""CSV sniffing and column profiling (stdlib only, deterministic)."""

from __future__ import annotations

import csv
import io
import math
import re
from collections import Counter
from pathlib import Path

from .models import ColumnProfile

PATTERNS = {
    "excel_sci": re.compile(r"^-?\d(\.\d+)?E\+\d+$"),
    "integer": re.compile(r"^-?\d+$"),
    "number": re.compile(r"^-?\d+[.,]\d+$"),
    "datetime": re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}"),
    "date": re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{2}[./]\d{2}[./]\d{4}$"),
    "email": re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    "url": re.compile(r"^https?://", re.I),
    "phone": re.compile(r"^'?\+?[\d\s().-]{7,}$"),
    "bool": re.compile(r"^(true|false|yes|no|oui|non)$", re.I),
}


def sniff(path: Path, *, encoding: str = "utf-8-sig", delimiter: str | None = None, header_row: int | None = None) -> tuple[str, int, list[str]]:
    """Delimiter, 0-based header row (title rows above it are skipped) and header."""
    head: list[str] = []
    with open(path, encoding=encoding, newline="") as fh:
        for _ in range(10):
            line = fh.readline()
            if not line:
                break
            head.append(line)
    text = "".join(head)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text, delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if header_row is None:
        counts = [sum(1 for c in r if c.strip()) for r in rows]
        best = max(counts) if counts else 0
        header_row = next((i for i, c in enumerate(counts) if c >= max(2, math.ceil(best * 0.5))), 0)
    header = [c.strip() for c in rows[header_row]] if header_row < len(rows) else []
    return delimiter, header_row, header


def read_rows(path: Path, *, encoding: str, delimiter: str, header_row: int, limit: int | None = None) -> tuple[list[str], list[dict]]:
    with open(path, encoding=encoding, newline="") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        for _ in range(header_row):
            next(reader, None)
        header = [c.strip() for c in next(reader)]
        rows: list[dict] = []
        for raw in reader:
            if not any(c.strip() for c in raw):
                continue
            rows.append({header[i]: (raw[i].strip() if i < len(raw) else "") for i in range(len(header))})
            if limit and len(rows) >= limit:
                break
    return header, rows


def infer_type(values: list[str]) -> tuple[str, list[str]]:
    """Majority vote over non-empty values; returns (type, flags)."""
    if not values:
        return "empty", []
    votes: Counter = Counter()
    flags: list[str] = []
    for v in values:
        for name in ("excel_sci", "bool", "email", "url", "datetime", "date", "number", "integer", "phone"):
            if PATTERNS[name].match(v):
                votes[name] += 1
                break
        else:
            votes["text"] += 1
    total = len(values)
    winner, count = votes.most_common(1)[0]
    if votes.get("excel_sci"):
        flags.append("excel_mangled_number")
    if count < total * 0.8 and winner != "text":
        flags.append("mixed_types")
    if winner in ("excel_sci", "integer") and votes.get("integer", 0) + votes.get("excel_sci", 0) >= total * 0.8:
        winner = "integer"
    return winner, flags


def profile_columns(header: list[str], rows: list[dict], *, enum_max: int = 12) -> list[ColumnProfile]:
    out: list[ColumnProfile] = []
    n = len(rows)
    for i, col in enumerate(header):
        values = [r.get(col, "") for r in rows]
        filled = [v for v in values if v != ""]
        distinct = Counter(filled)
        inferred, flags = infer_type(filled[:5000])
        unique = bool(filled) and len(distinct) == len(filled)
        if inferred == "integer" and unique and filled and min(len(v) for v in filled[:200]) >= 5:
            inferred = "id"
        enum_values = None
        if inferred == "text" and 0 < len(distinct) <= enum_max and len(filled) >= 5 and not unique and len(filled) >= 2 * len(distinct):
            inferred = "enum"
            enum_values = sorted(distinct)
        if inferred == "text" and unique and ({t for t in re.split(r"[^a-z0-9]+", col.lower()) if t} & {"id", "key", "uid", "guid", "ref"}):
            inferred = "id"
        if n and len(filled) < n * 0.05:
            flags.append("low_fill")
        if not filled:
            flags.append("empty")
        out.append(
            ColumnProfile(
                column=col, index=i, rows=n, filled=len(filled), fill_rate=round(len(filled) / n, 4) if n else 0.0,
                distinct=len(distinct), unique=unique, inferred_type=inferred,
                samples=[v[:60] for v, _ in distinct.most_common(3)], values=enum_values, flags=flags,
            )
        )
    return out
