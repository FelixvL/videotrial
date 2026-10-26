"""CineMarker — acteurs importeren uit CSV, TSV of een MySQL-tabeldump (geen Qt)."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

META_FIELDS = ("rating", "decennia", "kleur", "grootte", "voornaam", "achternaam")


def normalize_row(row: dict[str, str]) -> dict | None:
    """Eén rij (lowercase keys) → {'name', 'notes'} of None als er geen naam is.

    De naam is bij voorkeur de stam van AfbeeldingURL (komt overeen met de
    fotobestandsnaam), anders voornaam+achternaam aan elkaar.
    """
    img = (row.get("afbeeldingurl") or row.get("afbeelding") or "").strip()
    if img and img.lower() != "null":
        name = Path(img).stem
    else:
        name = ((row.get("voornaam") or "").strip() + (row.get("achternaam") or "").strip()).strip()
    if not name or name.lower() == "null":
        return None
    meta = {}
    for f in META_FIELDS:
        v = (row.get(f) or "").strip()
        if v and v.lower() != "null":
            meta[f] = v
    return {"name": name, "notes": json.dumps(meta, ensure_ascii=False) if meta else ""}


def parse_mysql_table(content: str) -> list[dict]:
    lines = [ln for ln in content.splitlines() if ln.strip().startswith("|")]
    if not lines:
        return []

    def split_row(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    headers = [h.lower().strip() for h in split_row(lines[0])]
    out = []
    for ln in lines[1:]:
        r = normalize_row(dict(zip(headers, split_row(ln))))
        if r:
            out.append(r)
    return out


def parse_dsv(content: str, delimiter: str = ",") -> list[dict]:
    reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
    out = []
    for row in reader:
        norm = {(k or "").lower().strip(): (v or "").strip() for k, v in row.items()}
        r = normalize_row(norm)
        if r:
            out.append(r)
    return out


def parse_import_text(content: str) -> list[dict]:
    first = content.lstrip().split("\n", 1)[0]
    if first.startswith("+") or "|" in first:
        return parse_mysql_table(content)
    if "\t" in first:
        return parse_dsv(content, "\t")
    return parse_dsv(content, ",")


def parse_import_file(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return parse_import_text(f.read())
