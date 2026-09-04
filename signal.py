"""
signal.py
=========
Format commun utilisé par TOUTES les stratégies (Niveau 1) et par la
couche IA (Niveau 2). C'est la pièce centrale qui permet aux deux
niveaux de s'emboîter sans que le reste du bot ait besoin de savoir
lequel des deux a produit la décision finale.
"""

from dataclasses import dataclass, field


@dataclass
class Signal:
    direction: str              # "buy", "sell", ou "none"
    conviction_score: float     # 0 à 100 -- à quel point le signal est fiable
    reasons: list = field(default_factory=list)   # ex: ["Croisement MM haussier (+30)", "ADX fort (+20)"]
    ai_reviewed: bool = False   # True si la couche IA a examiné ce signal
    ai_notes: str = ""          # commentaire de l'IA si elle a été appelée

    @property
    def is_actionable(self) -> bool:
        """Un signal ne compte que s'il a une direction ET un score suffisant."""
        return self.direction in ("buy", "sell")

    def add(self, points: float, reason: str):
        """Ajoute des points au score et note la raison -- pratique pour
        construire un score étape par étape dans les stratégies."""
        self.conviction_score += points
        self.reasons.append(f"{reason} ({'+' if points >= 0 else ''}{points})")


def no_signal() -> Signal:
    return Signal(direction="none", conviction_score=0.0)
