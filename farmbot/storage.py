"""Durable observations and profitability calculated from actual results."""
from __future__ import annotations

import csv
import math
import sqlite3
from collections import defaultdict
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import BattleRecord, Stage, StageStats


def _utc(value: str) -> str:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone")
    return instant.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _validate(record: BattleRecord) -> None:
    if not record.event_id or not record.run_id:
        raise ValueError("event_id and run_id are required")
    if record.outcome not in {"victory", "defeat", "interrupted"}:
        raise ValueError("Invalid battle outcome")
    if not math.isfinite(record.duration) or record.duration <= 0:
        raise ValueError("Battle duration must be finite and positive")
    if any(isinstance(amount, bool) or not isinstance(amount, int) or amount < 0
           for amount in (record.gold, record.exp)):
        raise ValueError("Rewards must be nonnegative integers")
    _utc(record.observed_at)


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), timeout=10)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS battles (
                event_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                duration REAL NOT NULL CHECK(duration > 0),
                gold INTEGER NOT NULL CHECK(gold >= 0),
                exp INTEGER NOT NULL CHECK(exp >= 0),
                outcome TEXT NOT NULL CHECK(outcome IN ('victory', 'defeat', 'interrupted')),
                observed_at TEXT NOT NULL,
                raw TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS battles_target_time
                ON battles(stage, difficulty, observed_at);
            CREATE TABLE IF NOT EXISTS catalog (
                stage TEXT NOT NULL,
                difficulty TEXT NOT NULL,
                PRIMARY KEY(stage, difficulty)
            );
        """)
        self._connection.commit()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def add(self, record: BattleRecord) -> bool:
        """Persist one observation; a repeated event_id is a harmless no-op."""
        _validate(record)
        values = asdict(record)
        values["observed_at"] = _utc(record.observed_at)
        if record.outcome == "interrupted":
            values["gold"] = values["exp"] = 0
        with self._connection:
            cursor = self._connection.execute("""
                INSERT INTO battles
                (event_id,run_id,stage,difficulty,duration,gold,exp,outcome,observed_at,raw)
                VALUES (:event_id,:run_id,:stage,:difficulty,:duration,:gold,:exp,:outcome,:observed_at,:raw)
                ON CONFLICT(event_id) DO NOTHING
            """, values)
        return cursor.rowcount == 1

    def records(self, stage: Stage | None = None, since: str | None = None) -> list[BattleRecord]:
        filters: list[str] = []
        parameters: list[str] = []
        if stage is not None:
            filters.append("stage=? AND difficulty=?")
            parameters.extend((stage.stage, stage.difficulty))
        if since is not None:
            filters.append("observed_at>=?")
            parameters.append(_utc(since))
        where = " WHERE " + " AND ".join(filters) if filters else ""
        rows = self._connection.execute(
            "SELECT * FROM battles" + where + " ORDER BY observed_at, event_id", parameters
        )
        return [BattleRecord(**dict(row)) for row in rows]

    def save_catalog(self, stages: Iterable[Stage]) -> None:
        targets = {(target.stage, target.difficulty) for target in stages}
        with self._connection:
            self._connection.execute("DELETE FROM catalog")
            self._connection.executemany("INSERT INTO catalog(stage,difficulty) VALUES (?,?)", sorted(targets))

    def catalog(self) -> list[Stage]:
        targets = [Stage(row[0], row[1]) for row in self._connection.execute("SELECT stage,difficulty FROM catalog")]
        return sorted(targets, key=lambda target: (target.position, target.difficulty))


def stats(records: Iterable[BattleRecord], min_samples: int = 5,
          min_win_rate: float = 0.8, *, battle_restart_seconds: float = 0.0) -> list[StageStats]:
    """Aggregate rewards / total observed time, including lost/interrupted time.

    ``samples`` includes interruptions; reliability requires ``min_samples``
    completed results (victories plus defeats). Interruptions also reduce the
    success ratio. Interrupted observations can never contribute rewards.
    Restart overhead is added once per completed result when calculating total
    and mean time and yield. Interrupted time already measures elapsed wall time.
    Stored/OCR battle durations are never changed.
    """
    if min_samples < 1 or not 0 <= min_win_rate <= 1:
        raise ValueError("Invalid reliability thresholds")
    if (isinstance(battle_restart_seconds, bool) or not isinstance(battle_restart_seconds, (int, float))
            or not math.isfinite(battle_restart_seconds) or battle_restart_seconds < 0):
        raise ValueError("Battle restart seconds must be finite and nonnegative")
    groups: dict[Stage, list[BattleRecord]] = defaultdict(list)
    seen: set[str] = set()
    for record in records:
        _validate(record)
        if record.event_id in seen:
            continue
        seen.add(record.event_id)
        groups[Stage(record.stage, record.difficulty)].append(record)
    result: list[StageStats] = []
    for target, group in groups.items():
        victories = sum(record.outcome == "victory" for record in group)
        defeats = sum(record.outcome == "defeat" for record in group)
        interruptions = sum(record.outcome == "interrupted" for record in group)
        gold = sum(record.gold for record in group if record.outcome != "interrupted")
        exp = sum(record.exp for record in group if record.outcome != "interrupted")
        seconds = sum(record.duration for record in group) + battle_restart_seconds * (victories + defeats)
        win_rate = victories / len(group)
        gold_per_second = gold / seconds
        result.append(StageStats(
            stage=target.stage, difficulty=target.difficulty, samples=len(group),
            victories=victories, defeats=defeats, interruptions=interruptions,
            total_gold=gold, total_exp=exp, total_seconds=seconds,
            mean_gold=gold / len(group), mean_seconds=seconds / len(group),
            gold_per_second=gold_per_second, gold_per_minute=gold_per_second * 60,
            gold_per_hour=gold_per_second * 3600, win_rate=win_rate,
            reliable=victories + defeats >= min_samples and win_rate >= min_win_rate,
            latest_at=max(_utc(record.observed_at) for record in group),
        ))
    return sorted(result, key=lambda item: (not item.reliable, -item.gold_per_minute,
                                            -item.win_rate, item.target.position, item.difficulty))


def export_ranking(ranking: Iterable[StageStats], path: str | Path) -> Path:
    """Write a UTF-8 BOM CSV that opens cleanly in Excel on Windows."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=[field.name for field in fields(StageStats)])
        writer.writeheader()
        writer.writerows(asdict(item) for item in ranking)
    return destination
