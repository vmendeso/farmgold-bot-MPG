"""Conservative OCR parsing and deduplication of the visible battle log."""
from __future__ import annotations

import math
import re
import unicodedata

from .models import LogEntry, LogMarker, ParsedBattle, UntimedDefeat


def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


_HEADER = re.compile(
    r"^\s*(\d{1,3}\s*[-–—]\s*\d{1,3})\s+"
    r"(concluida|concluido|vitoria|derrota|derrotado|derrotada|falhou)\b"
)
_TIME = re.compile(r"(?<![\d:])(\d{1,3}):(\d{2})(?:[.,](\d{1,3}))?(?![\d:])")
_NUMBER = r"(?:\d{1,3}(?:[.,\s]\d{3})+|\d+)"
_REWARDS = re.compile(
    rf"\+\s*(?P<gold>{_NUMBER})\s*[^\d+]*?"
    rf"\+\s*(?P<exp>{_NUMBER})\s*exp\b"
)
DEFAULT_NOTIFICATION_PATTERNS = (
    r"ca[il]xa(?: de ch[ae]fe)? (?:obtida|aberta)[.!]?",
    r"voce obteve [^\r\n+]{1,160}?\s+[x×]\s*[1-9]\d*[.!]?",
)
_RESULT_LIKE = re.compile(
    r"(?<!\d)\d{1,3}\s*[-–—]\s*\d{1,3}(?!\d)"
    r"|\d+\s*:\s*\d|\b(?:conclu\w*|derrot\w*|falhou|exp)\b"
)
_UNTIMED_DEFEAT = re.compile(r"([1-9]\d{0,2})\s*[-–—]\s*([1-9]\d{0,2})\s+falhou[.!]?")


def parse_battle(text: str) -> ParsedBattle | None:
    """Parse a complete Portuguese result row, without repairing guessed digits.

    A defeat with an explicit duration and no reward fields is recorded with
    zero rewards. A victory always needs both displayed reward fields. Missing
    or malformed reward fields never become a zero-reward victory.
    """
    normalized = _normalized(text).replace("\u00a0", " ")
    header = _HEADER.match(normalized)
    if header is None or re.search(r"\b(?:nao|pendente|interrompida|interrompido)\b", normalized):
        return None
    tail = normalized[header.end():]
    if re.search(r"\b(?:concluida|concluido|vitoria|derrota|derrotado|derrotada|falhou)\b", tail):
        return None
    # OCR can attach a middle-dot separator to the preceding numeric field
    # as ':'. It is a separator only when the next token is an explicit '+'.
    # The actual minutes:seconds colon and every numeric digit remain intact.
    tail = re.sub(r"(?<=\d):(?=\s*\+)", " · ", tail)
    duration_match = _TIME.search(tail)
    if duration_match is None:
        return None
    minutes, seconds = int(duration_match[1]), int(duration_match[2])
    fraction = float("0." + (duration_match[3] or "0"))
    duration = minutes * 60 + seconds + fraction
    if seconds >= 60 or duration <= 0:
        return None
    outcome = "victory" if header[2] in {"concluida", "concluido", "vitoria"} else "defeat"
    rewards_tail = tail[duration_match.end():]
    # The game's middle-dot separators can be read as standalone hyphens.
    # Remove only whitespace-delimited separators before locating rewards, so
    # an attached numeric negative cannot turn into a separator at a slice edge.
    # A small-font dot can also become '~-' (observed in '1:11.44~- +').
    # Permit punctuation before a separator, but still require whitespace
    # after it and never strip a sign attached to a number or another sign.
    rewards_tail = re.sub(r"(?<![\w+\-–—])[-–—](?!\S)", "", rewards_tail)
    rewards = _REWARDS.search(rewards_tail)
    if rewards is None:
        if outcome == "victory" or re.search(r"[\d+]|\bexp\b", rewards_tail):
            return None
        gold = exp = 0
    else:
        # No extra number or sign is permitted around the two reward fields.
        # This rejects OCR fragments such as '+1,36 · +3,055 EXP'.
        surrounding = rewards_tail[:rewards.start()] + rewards_tail[rewards.end():]
        if re.search(r"[\d+\-]", surrounding):
            return None
        gold = int(re.sub(r"\D", "", rewards["gold"]))
        exp = int(re.sub(r"\D", "", rewards["exp"]))
    stage = re.sub(r"\s", "", header[1]).replace("–", "-").replace("—", "-")
    if any(int(part) <= 0 for part in stage.split("-")):
        return None
    return ParsedBattle(stage, duration, gold, exp, outcome, text.strip())


def parse_log_entry(text: str, notification_patterns: list[str] | tuple[str, ...] | None = None
                    ) -> LogEntry | None:
    """Recognize a timed battle, exact untimed defeat or known notification.

    Additional patterns are full-matched against lowercase, accent-free text
    with collapsed whitespace. They extend the built-in Portuguese loot forms.
    Only the observed complete '<stage> falhou' form is an untimed defeat;
    partial time/reward fields must not fall back to that representation.
    Unrecognized rows and malformed result-like rows remain failed OCR reads;
    they must never disappear from a snapshot or silently become notifications.
    """
    battle = parse_battle(text)
    if battle is not None:
        return battle
    normalized = " ".join(_normalized(text).split())
    defeat = _UNTIMED_DEFEAT.fullmatch(normalized)
    if defeat is not None:
        return UntimedDefeat(f"{int(defeat[1])}-{int(defeat[2])}", text.strip())
    if not normalized or _RESULT_LIKE.search(normalized):
        return None
    if notification_patterns is None:
        notification_patterns = ()
    if (not isinstance(notification_patterns, (list, tuple))
            or any(not isinstance(pattern, str) or not pattern.strip() for pattern in notification_patterns)):
        raise ValueError("notification_patterns must be a list of nonempty regular expressions")
    for pattern in (*DEFAULT_NOTIFICATION_PATTERNS, *notification_patterns):
        compiled = re.compile(pattern, re.IGNORECASE)
        if compiled.fullmatch("") is not None:
            raise ValueError("Notification patterns must not match empty text")
        if compiled.fullmatch(normalized) is not None:
            return LogMarker(text.strip())
    return None


class LogTracker:
    """Identify newly appended rows using a sequence overlap, never a set.

    ``elapsed_seconds`` is wall time since the previous ``update`` call.
    Returned battle and notification rows are chronological regardless of the
    displayed log order. Notification occurrences keep their sequence positions
    and can anchor battle movement, but do not contribute any battle duration.
    Baselines, missing rows and unmatched snapshots do not generate events.
    When a not-yet-full log grows, all previous rows must remain as its exact
    chronological prefix before appended rows can be counted. A complete new
    snapshot can establish a fresh baseline after a discontinuity. Identical
    full logs cannot reveal whether another
    identical battle occurred; those unobservable events are deliberately not
    counted. Poll frequently enough to retain an overlap between snapshots.
    """

    def __init__(self, order: str = "newest_first", max_gap_seconds: float = 120.0,
                 timing_tolerance_seconds: float = 1.0) -> None:
        if order not in {"newest_first", "oldest_first"}:
            raise ValueError("order must be newest_first or oldest_first")
        if not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        if not math.isfinite(timing_tolerance_seconds) or timing_tolerance_seconds < 0:
            raise ValueError("timing_tolerance_seconds must be nonnegative")
        self.order = order
        self.max_gap_seconds = max_gap_seconds
        self.timing_tolerance_seconds = timing_tolerance_seconds
        self.reset()

    def reset(self) -> None:
        self._previous: list[LogEntry] = []
        self._unobserved_seconds = 0.0
        self.diagnostics = "awaiting_baseline"

    def _chronological(self, rows: list[LogEntry]) -> list[LogEntry]:
        return list(reversed(rows)) if self.order == "newest_first" else list(rows)

    @staticmethod
    def _minimum_elapsed(entries: list[LogEntry]) -> float:
        # A battle can already be running when one or more loot notifications
        # appear. Exempt the first BATTLE, not simply the first log row.
        # An untimed defeat occupies a battle position without supplying a
        # guessed duration; following timed battles must still fit the interval.
        first_battle = True
        minimum = 0.0
        for entry in entries:
            if isinstance(entry, (ParsedBattle, UntimedDefeat)):
                if not first_battle and isinstance(entry, ParsedBattle):
                    minimum += entry.duration
                first_battle = False
        return minimum

    def prime(self, rows: list[LogEntry]) -> None:
        self._previous = self._chronological(rows)
        self._unobserved_seconds = 0.0
        self.diagnostics = "baseline" if rows else "awaiting_baseline"

    def update(self, rows: list[LogEntry], elapsed_seconds: float) -> list[LogEntry]:
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
            raise ValueError("elapsed_seconds must be finite and nonnegative")
        self._unobserved_seconds += elapsed_seconds
        current = self._chronological(rows)
        if not current:
            self.diagnostics = "empty_ocr"
            return []
        if not self._previous:
            self.prime(rows)
            return []
        if len(current) < len(self._previous):
            self.diagnostics = "partial_ocr"
            return []
        if self._unobserved_seconds > self.max_gap_seconds:
            self.prime(rows)
            self.diagnostics = "observation_gap_rebased"
            return []
        old = [battle.fingerprint for battle in self._previous]
        new = [battle.fingerprint for battle in current]
        if len(current) > len(self._previous):
            additions = current[len(old):]
            anchored = new[:len(old)] == old
            feasible = self._minimum_elapsed(additions) <= (
                self._unobserved_seconds + self.timing_tolerance_seconds)
            self.prime(rows)
            if anchored and feasible:
                self.diagnostics = "new_results_log_grew"
                return additions
            self.diagnostics = "row_count_changed_without_anchor_rebased"
            return []
        if new == old:
            self._unobserved_seconds = 0.0
            self.diagnostics = "unchanged_pixels_cannot_distinguish_new_occurrences"
            return []
        possible: list[list[LogEntry]] = []
        for overlap in range(1, len(old)):
            if old[-overlap:] != new[:overlap]:
                continue
            additions = current[overlap:]
            # The first battle might already have been in progress at the last
            # capture. Every later result must fit entirely into this interval.
            minimum_time = self._minimum_elapsed(additions)
            if minimum_time <= self._unobserved_seconds + self.timing_tolerance_seconds:
                possible.append(additions)
        self.prime(rows)
        if len(possible) == 1:
            self.diagnostics = "new_results"
            return possible[0]
        self.diagnostics = "ambiguous_overlap_rebased" if possible else "no_feasible_overlap_rebased"
        return []
