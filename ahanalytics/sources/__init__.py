"""Datenquellen.

Jede Quelle schreibt über die gemeinsamen Helfer in ``ahanalytics.db``:

1. ``db.upsert_house(...)``  – Auktionshaus/Realm anlegen
2. ``db.write_snapshot(...)`` – Kennzahlen pro Item für einen Zeitpunkt
3. ``db.refresh_summary(...)`` + ``db.bump_data_version(...)``

Eine neue Quelle (z. B. ein eigenes Export-Addon oder eine Drittanbieter-API)
muss nur diese drei Schritte ausführen und ein ``ImportResult`` liefern.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImportResult:
    source: str
    houses: int = 0
    snapshots: int = 0
    items: int = 0
    price_points: int = 0
    skipped: int = 0
    messages: list[str] = field(default_factory=list)

    def summary(self) -> str:
        msg = (f"{self.source}: {self.houses} Häuser, {self.snapshots} Snapshots, "
               f"{self.items} Items, {self.price_points} Preispunkte")
        if self.skipped:
            msg += f", {self.skipped} übersprungen"
        if self.messages:
            msg += " – " + "; ".join(self.messages[:5])
        return msg

    def as_dict(self):
        return {**self.__dict__, "summary": self.summary()}
