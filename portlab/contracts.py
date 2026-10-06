from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any


@dataclass
class TerminalConfig:
    port: str = "Guam"
    horizon_hours: float = 336.0
    berths: int = 2
    cranes: int = 3
    cranes_per_vessel: int = 1
    crane_moves_per_hour: float = 15.0
    tractors: int = 8
    tractor_cycle_minutes: float = 8.0
    yard_capacity_boxes: int = 1500
    import_dwell_hours: float = 48.0
    export_lead_hours: float = 12.0
    max_vessel_length_m: float = 300.0
    max_vessel_draft_m: float = 10.0
    pressure_queue_calls: int = 1
    observation_hours: float = 168.0
    persistence_hours: float = 24.0
    demand_multiplier: float = 1.0
    seed: int = 42
    replications: int = 6
    service_variability: float = 0.15
    view_fps: int = 30
    max_visual_containers: int = 120
    initial_yard_boxes: int = 0
    notes: str = "Illustrative prototype assumptions; confirm dated operating parameters."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        if not isinstance(self.port, str) or not self.port.strip():
            raise ValueError("Port must be a nonblank name")
        if not isinstance(self.notes, str):
            raise ValueError("Parameter notes must be text")
        def finite_number(value):
            return (not isinstance(value, bool) and isinstance(value, (int, float))
                    and math.isfinite(value))
        positives = ("horizon_hours", "crane_moves_per_hour", "tractor_cycle_minutes",
                     "max_vessel_length_m", "max_vessel_draft_m")
        for name in positives:
            value = getattr(self, name)
            if not finite_number(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        for name in ("berths", "cranes", "cranes_per_vessel", "tractors", "yard_capacity_boxes", "replications", "pressure_queue_calls"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("seed", "initial_yard_boxes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if type(self.view_fps) is not int or not 5 <= self.view_fps <= 30:
            raise ValueError("Playback frame-rate cap must be an integer from 5 to 30")
        if type(self.max_visual_containers) is not int or not 1 <= self.max_visual_containers <= 120:
            raise ValueError("Maximum container visuals must be an integer from 1 to 120")
        if self.cranes_per_vessel > self.cranes:
            raise ValueError("Cranes per vessel cannot exceed the available crane fleet")
        for name in ("import_dwell_hours", "export_lead_hours", "observation_hours", "persistence_hours", "demand_multiplier", "service_variability"):
            value = getattr(self, name)
            if not finite_number(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.demand_multiplier <= 0:
            raise ValueError("Demand multiplier must be greater than zero")
        if self.initial_yard_boxes > self.yard_capacity_boxes:
            raise ValueError("Initial yard inventory exceeds capacity")
        if not 0 <= self.service_variability <= 1:
            raise ValueError("Service variability must be between 0 and 1")
        if self.pressure_queue_calls < 1:
            raise ValueError("Pressure queue threshold must be at least one waiting call")

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> 'TerminalConfig':
        if not isinstance(values, dict):
            raise ValueError("Settings must be a JSON object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(values) - allowed
        if unknown:
            raise ValueError("Unknown settings: " + ", ".join(sorted(unknown)))
        config = cls(**values)
        config.validate()
        return config


@dataclass
class VesselCall:
    vessel_id: str
    arrival_hour: float
    import_boxes: int
    export_boxes: int
    length_m: float = 180.0
    draft_m: float = 8.0
    box40_share: float = 0.5
    port: str = "Guam"
    evidence_type: str = "synthetic"
    observed_berth_start_hour: float | None = None
    observed_departure_hour: float | None = None


@dataclass
class DatasetBundle:
    calls: list[VesselCall] = field(default_factory=list)
    annual_series: dict[tuple[str, str], list[tuple[int, float]]] = field(default_factory=dict)
    evidence_notes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    config: TerminalConfig
    vessels: list[dict[str, Any]]
    intervals: list[dict[str, Any]]
    events: list[dict[str, Any]]
    kpis: dict[str, Any]
    checks: dict[str, Any]
    evidence_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value['schema_version'] = 1
        return value
