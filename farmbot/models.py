from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import unicodedata


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, order=True)
class Stage:
    stage: str
    difficulty: str

    @property
    def key(self) -> str:
        return f"{self.stage}|{self.difficulty}"

    @property
    def position(self) -> tuple[int, int]:
        act, level = self.stage.split("-")
        return int(act), int(level)


@dataclass(frozen=True)
class ParsedBattle:
    stage: str
    duration: float
    gold: int
    exp: int
    outcome: str  # victory or defeat, only visually confirmed results
    raw: str = ""

    @property
    def fingerprint(self) -> tuple:
        return (self.stage, round(self.duration, 2), self.gold, self.exp, self.outcome)


@dataclass(frozen=True)
class LogMarker:
    """A recognized non-battle row that anchors movement in the shared log.

    Markers carry no measured reward or elapsed battle time. Equal marker text
    can still occur in multiple separate rows; a fingerprint is not an event ID.
    """

    raw: str

    @property
    def duration(self) -> float:
        return 0.0

    @property
    def fingerprint(self) -> tuple[str, str]:
        normalized = unicodedata.normalize("NFKD", self.raw.casefold())
        text = "".join(char for char in normalized if not unicodedata.combining(char))
        text = " ".join(text.split()).rstrip(" .!")
        # Only the observed OCR variants of the explicit box-message prefix
        # are interchangeable. Item names and all numerical data stay intact.
        if text.startswith("calxa "):
            text = "caixa " + text[len("calxa "):]
        if text.startswith("caixa de chafe "):
            text = "caixa de chefe " + text[len("caixa de chafe "):]
        return "notification", text


@dataclass(frozen=True)
class UntimedDefeat:
    """An explicit failed-stage row with no displayed duration or rewards.

    It anchors the log sequence, but cannot become a timed battle record.
    """

    stage: str
    raw: str

    @property
    def fingerprint(self) -> tuple[str, str]:
        return "untimed_defeat", self.stage


LogEntry = ParsedBattle | LogMarker | UntimedDefeat


@dataclass(frozen=True)
class BattleRecord:
    event_id: str
    run_id: str
    stage: str
    difficulty: str
    duration: float
    gold: int
    exp: int
    outcome: str  # victory, defeat, interrupted
    observed_at: str
    raw: str = ""


@dataclass(frozen=True)
class StageStats:
    stage: str
    difficulty: str
    samples: int
    victories: int
    defeats: int
    interruptions: int
    total_gold: int
    total_exp: int
    total_seconds: float
    mean_gold: float
    mean_seconds: float
    gold_per_second: float
    gold_per_minute: float
    gold_per_hour: float
    win_rate: float
    reliable: bool
    latest_at: str

    @property
    def target(self) -> Stage:
        return Stage(self.stage, self.difficulty)
