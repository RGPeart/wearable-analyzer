"""Source-neutral data model. Every adapter produces a :class:`Dataset`."""

from dataclasses import dataclass, field
from datetime import date, datetime

LB_PER_KG = 1 / 0.45359237


@dataclass(frozen=True)
class Workout:
    source: str  # which adapter produced it: apple_health, fitbod, gpx, or "fitbod+apple_health" once merged
    sport: str
    start: datetime
    end: datetime
    duration_s: float  # 0.0 when unknown (a Fitbod session with no matching Apple workout)
    distance_m: float | None = None
    energy_kcal: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    elevation_gain_m: float | None = None
    app: str | None = None  # app that wrote it (Apple's sourceName), e.g. "Fitbod", "Nike Run Club"
    imported: bool = False  # a copy of another app's workout; dropped when the original is present


@dataclass(frozen=True)
class StrengthSet:
    session_start: datetime  # joins the set to its Workout
    order: int  # 1-based position within the session, in the order performed
    exercise: str
    reps: int
    weight_kg: float
    multiplier: float  # 2.0 for a pair of dumbbells, 0.0 for bodyweight/holds
    duration_s: float = 0.0
    distance_m: float = 0.0
    is_warmup: bool = False

    @property
    def weight_lb(self) -> float:
        return self.weight_kg * LB_PER_KG

    @property
    def volume_kg(self) -> float:
        """reps x weight x multiplier. Fitbod does not document the multiplier; this is the common reading."""
        return self.reps * self.weight_kg * self.multiplier


@dataclass(frozen=True)
class DailyMetric:
    day: date
    steps: int | None = None
    resting_hr: float | None = None
    hrv_ms: float | None = None


@dataclass(frozen=True)
class SleepNight:
    night: date  # see wearables.tz.local_night
    asleep_s: float
    in_bed_only: bool = False  # no asleep stages recorded (pre-iOS 16); asleep_s is time in bed


@dataclass
class Dataset:
    workouts: list[Workout] = field(default_factory=list)
    daily: list[DailyMetric] = field(default_factory=list)
    sleep: list[SleepNight] = field(default_factory=list)
    sets: list[StrengthSet] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: "Dataset") -> None:
        self.workouts += other.workouts
        self.daily += other.daily
        self.sleep += other.sleep
        self.sets += other.sets
        self.warnings += other.warnings
