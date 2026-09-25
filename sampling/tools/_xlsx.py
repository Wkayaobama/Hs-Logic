"""Minimal .xlsx reader (stdlib only) so the derivation tools run anywhere PowerShell does."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path


def read_sheets(path: str | Path, wanted: set[str] | None = None) -> dict[str, list[list[str]]]:
    z = zipfile.ZipFile(path)
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        ss = z.read("xl/sharedStrings.xml").decode("utf-8")
        for si in re.findall(r"<si>(.*?)</si>", ss, re.S):
            shared.append(html.unescape("".join(re.findall(r"<t[^>]*>([^<]*)</t>", si))))
    wb = z.read("xl/workbook.xml").decode("utf-8")
    sheets = re.findall(r'<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"', wb)
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    relmap: dict[str, str] = {}
    for rel in re.findall(r"<Relationship [^>]*/>", rels):
        rid = re.search(r'Id="([^"]+)"', rel)
        target = re.search(r'Target="([^"]+)"', rel)
        if rid and target:
            relmap[rid.group(1)] = target.group(1)

    def cell_value(cell: str) -> str:
        t = re.search(r' t="([^"]+)"', cell)
        v = re.search(r"<v>([^<]*)</v>", cell)
        if not v:
            inline = re.search(r"<is>.*?<t[^>]*>([^<]*)</t>", cell, re.S)
            return html.unescape(inline.group(1)) if inline else ""
        if t and t.group(1) == "s":
            return shared[int(v.group(1))]
        return html.unescape(v.group(1))

    out: dict[str, list[list[str]]] = {}
    for name, rid in sheets:
        if wanted and name not in wanted:
            continue
        target = relmap[rid].lstrip("/")
        target = target if target.startswith("xl/") else "xl/" + target
        xml = z.read(target).decode("utf-8")
        rows: list[list[str]] = []
        for row in re.findall(r"<row [^>]*>(.*?)</row>", xml, re.S):
            cells = re.findall(r"<c [^>]*?(?:/>|>.*?</c>)", row, re.S)
            values: list[str] = []
            for c in cells:
                ref = re.search(r' r="([A-Z]+)\d+"', c)
                col = _col_index(ref.group(1)) if ref else len(values)
                while len(values) < col:
                    values.append("")
                values.append(cell_value(c))
            rows.append(values)
        out[name] = rows
    return out


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1
