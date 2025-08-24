import csv
import os
from typing import Dict, List, Any
from rapidfuzz import fuzz, process


def load_synonyms(path: str) -> Dict[str, str]:
    """Load synonyms CSV into a dictionary."""
    if not os.path.exists(path):
        return {}
    syn: Dict[str, str] = {}
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            alias = row.get('alias', '').strip().lower()
            canonical = row.get('canonical', '').strip().lower()
            if alias and canonical:
                syn[alias] = canonical
    return syn


def _apply_synonyms(text: str, syn: Dict[str, str]) -> str:
    words = text.lower().split()
    replaced = [syn.get(w, w) for w in words]
    return ' '.join(replaced)


def build_search_strings(row: Any) -> str:
    parts: List[str] = []
    for key in ('sku', 'name', 'brand', 'category'):
        val = str(row.get(key, '')).strip()
        if val:
            parts.append(val)
    width = row.get('width_mm')
    if width and width == width:  # not NaN
        parts.append(f"{int(width)}mm" if isinstance(width, (int, float)) else str(width))
    length = row.get('length_m')
    if length and length == length:
        parts.append(f"{length}m" if isinstance(length, (int, float)) else str(length))
    return ' '.join(p.lower() for p in parts if p)


def match_line(raw_line: str, catalog_df, synonyms: Dict[str, str]):
    """Match a customer line against the catalog DataFrame.

    Returns a dict with 'best' and 'candidates'. Each candidate has
    keys: sku, name, confidence, reason.
    """
    norm_line = _apply_synonyms(raw_line, synonyms)
    choices = catalog_df['_search'].tolist()
    matches = process.extract(
        norm_line,
        choices,
        scorer=fuzz.token_set_ratio,
        limit=5,
    )
    candidates = []
    for choice, score, idx in matches:
        row = catalog_df.iloc[idx]
        candidates.append({
            'sku': row['sku'],
            'name': row['name'],
            'confidence': score,
            'reason': 'fuzzy match',
        })
    best = candidates[0] if candidates else None
    return {'best': best, 'candidates': candidates}
