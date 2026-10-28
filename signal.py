from dataclasses import dataclass, field


@dataclass
class Signal:
    direction: str
    conviction_score: float
    reasons: list = field(default_factory=list)
    ai_reviewed: bool = False
    ai_notes: str = ""
    ai_size_factor: float = 1.0
    ai_sl_tightness: float = 1.0
    ai_tp_extension: float = 1.0
    atr: float = 0.0   # ATR fourni par la stratégie (optionnel) pour calculer SL/TP

    @property
    def is_actionable(self) -> bool:
        return self.direction in ("buy", "sell")

    def add(self, points, reason):
        self.conviction_score += points
        self.reasons.append(f"{reason} ({'+' if points >= 0 else ''}{points})")


def no_signal():
    return Signal(direction="none", conviction_score=0.0)
