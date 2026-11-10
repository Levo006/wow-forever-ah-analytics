"""Verdichtet einzelne Auktionen eines Snapshots zu Kennzahlen pro Item."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


def weighted_percentile(levels: list[tuple[int, int]], q: float) -> int | None:
    """Mengengewichtetes Perzentil über sortierte (preis, menge)-Stufen.

    Definition: kleinster Preis, bei dem die kumulierte Menge >= q * Gesamtmenge.
    """
    total = sum(qty for _, qty in levels)
    if total <= 0:
        return None
    target = q * total
    cum = 0
    for price, qty in levels:
        cum += qty
        if cum >= target:
            return price
    return levels[-1][0]


def stats_from_levels(levels: list[tuple[int, int]], auctions: int | None = None) -> dict:
    """``levels``: Liste (stückpreis, menge), beliebig sortiert, Preise in Kupfer."""
    levels = sorted((int(p), int(q)) for p, q in levels if p is not None and q and q > 0)
    if not levels:
        return {}
    # gleiche Preise zusammenfassen
    merged: list[list[int]] = []
    for p, q in levels:
        if merged and merged[-1][0] == p:
            merged[-1][1] += q
        else:
            merged.append([p, q])
    qty = sum(q for _, q in merged)
    mean = sum(p * q for p, q in merged) / qty
    return {
        "min": merged[0][0],
        "p10": weighted_percentile(merged, 0.10),
        "median": weighted_percentile(merged, 0.50),
        "mean": int(round(mean)),
        "max": merged[-1][0],
        "quantity": qty,
        "auctions": auctions if auctions is not None else len(levels),
        "_levels": merged,
    }


def aggregate_listings(listings: Iterable[tuple[int, int, int]], ladder_depth: int = 40):
    """``listings``: (item_id, stückpreis, menge) je Auktion.

    Gibt (stats, ladders) zurück. ``ladders`` enthält die ersten
    ``ladder_depth`` Preisstufen je Item.
    """
    per_item: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for item_id, unit, qty in listings:
        if unit is None or unit <= 0 or not qty:
            continue
        per_item[int(item_id)].append((int(unit), int(qty)))
    stats, ladders = {}, {}
    for iid, lv in per_item.items():
        s = stats_from_levels(lv, auctions=len(lv))
        if not s:
            continue
        levels = s.pop("_levels")
        stats[iid] = s
        ladders[iid] = levels[:ladder_depth]
    return stats, ladders
