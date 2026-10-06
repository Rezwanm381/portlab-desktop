"""Counted discrete-event terminal model and conditional scenario comparisons.

All clocks are hours and all cargo counts are boxes.  This prototype has a
bounded aggregate apron, homogeneous berth limits, and explicit import dwell.
Its stock/flow series are measurements of the DES, not calibrated SD feedback.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import hashlib
import math
import random
import statistics
import time
from typing import Any, Callable

import simpy

from .contracts import SimulationResult, TerminalConfig, VesselCall

REQUIRED_PHYSICAL_CHECKS = (
    "import_conserved", "export_conserved", "future_export_conserved", "initial_inventory_conserved",
    "yard_inventory_consistent", "yard_capacity_respected", "yard_slot_balance",
    "apron_slot_balance", "apron_capacity_respected", "resource_bounds_respected", "inventory_nonnegative",
)
MAX_HANDLING_BATCHES = 30_000
MAX_IN_HORIZON_CALLS = 10_000


class QueuePressureGate:
    """Exact continuous raw-queue gate; a false signal resets persistence."""

    def __init__(self, observation_hours: float = 168.0, persistence_hours: float = 24.0,
                 threshold: int = 1):
        self.observation_hours = observation_hours
        self.persistence_hours = persistence_hours
        self.threshold = threshold
        self.pressure_since: float | None = None
        self.first_eligible_hour: float | None = None
        self.episodes: list[dict[str, Any]] = []
        self._last_time = 0.0
        self.pressure_exposure_hours = 0.0
        self.eligible_exposure_hours = 0.0
        self.raw_pressure = False

    def target_hour(self) -> float | None:
        if self.pressure_since is None:
            return None
        return max(self.observation_hours, self.pressure_since + self.persistence_hours)

    def eligible(self, hour: float) -> bool:
        target = self.target_hour()
        return target is not None and hour >= target

    def advance(self, hour: float) -> None:
        if hour < self._last_time:
            raise ValueError("Gate time cannot go backwards")
        if self.raw_pressure:
            self.pressure_exposure_hours += hour - self._last_time
            target = self.target_hour()
            if target is not None:
                self.eligible_exposure_hours += max(0.0, hour - max(self._last_time, target))
                if hour >= target and self.first_eligible_hour is None:
                    self.first_eligible_hour = target
        self._last_time = hour

    def update(self, hour: float, queued_calls: int) -> None:
        self.advance(hour)
        signal = queued_calls >= self.threshold
        if signal and not self.raw_pressure:
            self.pressure_since = hour
            self.episodes.append({"start_hour": hour, "end_hour": None,
                                  "eligible_hour": max(self.observation_hours,
                                                       hour + self.persistence_hours)})
        elif not signal and self.raw_pressure:
            self.episodes[-1]["end_hour"] = hour
            target = self.target_hour()
            if target is not None and hour < target:
                self.episodes[-1]["eligible_hour"] = None
            self.pressure_since = None
        self.raw_pressure = signal
        self.advance(hour)

    def status(self, hour: float) -> str:
        if hour < self.observation_hours:
            return "observing"
        if not self.raw_pressure:
            return "pressure_cleared"
        return "eligible" if self.eligible(hour) else "building_persistence"


def _factor(seed: int, vessel_id: str, flow: str, batch_start: int, stage: str,
            variability: float) -> float:
    """Stable entity/stage draw: resource scheduling never changes another draw."""
    if variability == 0:
        return 1.0
    key = f"{seed}|{vessel_id}|{flow}|{batch_start}|{stage}".encode("utf-8")
    draw_seed = int.from_bytes(hashlib.blake2b(key, digest_size=16).digest(), "big")
    sigma = math.sqrt(math.log1p(variability * variability))
    return random.Random(draw_seed).lognormvariate(-0.5 * sigma * sigma, sigma)


def _cancelled(cancel: Any) -> bool:
    if cancel is None:
        return False
    return bool(cancel() if callable(cancel) else cancel.is_set())


def _notify(progress: Callable | None, fraction: float, message: str) -> None:
    if progress is not None:
        progress(float(fraction), message)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _selected_calls(calls: list[VesselCall], config: TerminalConfig,
                    include_future: bool = False) -> list[VesselCall]:
    selected = []
    seen = set()
    numerator, denominator = float(config.demand_multiplier).as_integer_ratio()
    def scaled(value: int) -> int:
        return (value * numerator * 2 + denominator) // (denominator * 2)
    for call in calls:
        if call.port.casefold() != config.port.casefold():
            continue
        if not call.vessel_id or call.vessel_id in seen:
            raise ValueError("Vessel IDs must be nonempty and unique within a port")
        seen.add(call.vessel_id)
        for name in ("arrival_hour", "length_m", "draft_m", "box40_share"):
            value = getattr(call, name)
            if not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f"{call.vessel_id}: {name} must be finite")
        if call.arrival_hour < 0 or call.length_m <= 0 or call.draft_m <= 0:
            raise ValueError(f"{call.vessel_id}: arrival must be nonnegative; dimensions positive")
        if not 0 <= call.box40_share <= 1:
            raise ValueError(f"{call.vessel_id}: box40_share must be between zero and one")
        for name in ("import_boxes", "export_boxes"):
            value = getattr(call, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{call.vessel_id}: {name} must be a nonnegative integer")
        for name in ("observed_berth_start_hour", "observed_departure_hour"):
            value = getattr(call, name, None)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or
                                      not math.isfinite(value) or value < call.arrival_hour):
                raise ValueError(f"{call.vessel_id}: {name} must be finite and at or after arrival")
        if (getattr(call, "observed_berth_start_hour", None) is not None and
                getattr(call, "observed_departure_hour", None) is not None and
                call.observed_departure_hour < call.observed_berth_start_hour):
            raise ValueError(f"{call.vessel_id}: observed departure precedes observed berth start")
        if include_future or call.arrival_hour <= config.horizon_hours:
            selected.append(replace(call,
                import_boxes=scaled(call.import_boxes), export_boxes=scaled(call.export_boxes)))
    return sorted(selected, key=lambda call: (call.arrival_hour, call.vessel_id))


def run_simulation(calls: list[VesselCall], config: TerminalConfig,
                   capture_events: bool = True, progress: Callable | None = None,
                   cancel: Any = None) -> SimulationResult:
    """Run a fresh terminal model with conservation and right-censor reporting.

    At high volumes, one handling job counts several sequential box moves on
    its allocated resource. Batch sizes are explicitly reported; scenarios
    have identical batch definitions and per-entity random factors.
    """
    wall_start = time.perf_counter()
    config.validate()
    validated_trace = _selected_calls(calls, config, include_future=True)
    selected = [call for call in validated_trace if call.arrival_hour <= config.horizon_hours]
    future_staging_calls = [call for call in validated_trace
                            if call.arrival_hour > config.horizon_hours and call.export_boxes > 0 and
                            max(0.0, call.arrival_hour - config.export_lead_hours) <= config.horizon_hours and
                            call.length_m <= config.max_vessel_length_m and call.draft_m <= config.max_vessel_draft_m]
    future_staging_ids = {call.vessel_id for call in future_staging_calls}
    if len(selected) > MAX_IN_HORIZON_CALLS:
        raise ValueError(f"This run contains {len(selected):,} calls; the desktop prototype limit is "
                         f"{MAX_IN_HORIZON_CALLS:,}. Shorten the horizon or split the project.")
    env = simpy.Environment()
    berth_pool = simpy.Store(env, capacity=config.berths)
    berth_pool.items.extend(range(config.berths))
    crane_pool = simpy.Store(env, capacity=config.cranes)
    crane_pool.items.extend(range(config.cranes))
    tractor_pool = simpy.Store(env, capacity=config.tractors)
    tractor_pool.items.extend(range(config.tractors))
    yard_slots = simpy.Container(env, capacity=config.yard_capacity_boxes,
                                init=config.yard_capacity_boxes - config.initial_yard_boxes)
    # Resolve from the entire uploaded port trace, including future calls, so
    # extending the horizon never changes earlier handling jobs or random draws.
    total_boxes = sum(call.import_boxes + call.export_boxes for call in validated_trace)
    # Appointment staging cannot consume the space needed by working vessels.
    prestage_capacity = config.yard_capacity_boxes // 2
    prestage_slots = simpy.Container(env, capacity=max(1, prestage_capacity), init=prestage_capacity)
    batch_size = min(max(1, config.yard_capacity_boxes // 2), 250,
                     max(1, (total_boxes + 5999) // 6000))
    planned_batches = sum((call.import_boxes + batch_size - 1) // batch_size +
                          (call.export_boxes + batch_size - 1) // batch_size for call in selected)
    planned_batches += sum((call.export_boxes + batch_size - 1) // batch_size for call in future_staging_calls)
    if planned_batches > MAX_HANDLING_BATCHES:
        raise ValueError(f"This run would create {planned_batches:,} counted handling jobs; the "
                         f"desktop prototype limit is {MAX_HANDLING_BATCHES:,}. Shorten the horizon, "
                         "split the workload, or use a smaller scenario before running.")
    apron_capacity = max(batch_size, config.tractors * 2 * batch_size)
    apron_slots = simpy.Container(env, capacity=apron_capacity, init=apron_capacity)
    gate = QueuePressureGate(config.observation_hours, config.persistence_hours,
                             config.pressure_queue_calls)
    events: list[dict[str, Any]] = []
    intervals: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    waiting: set[str] = set()
    working: set[str] = set()
    queue_peak = 0
    gate_generation = 0
    trace_skipped = 0
    trace_limit = 150_000
    yard_physical = config.initial_yard_boxes
    peak_yard = yard_physical
    inventory = {"import_on_vessel": sum(call.import_boxes for call in selected),
                 "import_apron": 0, "import_on_tractor": 0, "import_yard": 0,
                 "import_released": 0,
                 "export_not_staged": sum(call.export_boxes for call in selected),
                 "export_yard": 0, "export_on_tractor": 0, "export_apron": 0,
                 "export_loaded": 0, "initial_yard": config.initial_yard_boxes,
                 "initial_released": 0, "import_reserved_yard": 0,
                 "import_reserved_apron": 0, "export_reserved_apron": 0,
                 "future_export_not_staged": sum(call.export_boxes for call in future_staging_calls),
                 "future_export_yard": 0}
    completed_count = 0
    batch_count = 0
    rows: dict[str, dict[str, Any]] = {}
    berth_ready: dict[str, Any] = {call.vessel_id: env.event() for call in selected + future_staging_calls}
    incompatible_ids: set[str] = set()
    cargo_changed = env.event()

    def wake_cargo() -> None:
        nonlocal cargo_changed
        previous = cargo_changed
        cargo_changed = env.event()
        previous.succeed()

    def emit(event_type: str, **fields: Any) -> None:
        nonlocal trace_skipped
        if not capture_events:
            return
        if len(events) >= trace_limit:
            trace_skipped += 1
            return
        events.append({"time": float(env.now), "type": event_type, **fields})

    def interval(resource: str, resource_id: int, start: float, end: float,
                 **fields: Any) -> None:
        # Resource intervals are retained for utilization even without the visual trace.
        intervals.append({"resource": resource, "resource_id": resource_id,
                          "start_time": float(start), "end_time": float(end), **fields})

    def gate_wakeup(generation: int, target: float):
        yield env.timeout(max(0.0, target - env.now))
        if generation == gate_generation and gate.eligible(env.now):
            gate.advance(env.now)
            emit("pressure_eligible", queued_calls=len(waiting), eligible_hour=target)

    def queue_changed() -> None:
        nonlocal queue_peak, gate_generation
        queue_peak = max(queue_peak, len(waiting))
        was_pressure = gate.raw_pressure
        gate.update(env.now, len(waiting))
        emit("queue_change", queued_calls=len(waiting), raw_pressure=gate.raw_pressure)
        if gate.raw_pressure != was_pressure:
            gate_generation += 1
            target = gate.target_hour()
            if target is not None:
                env.process(gate_wakeup(gate_generation, target))

    def compatible(call: VesselCall) -> bool:
        return (call.length_m <= config.max_vessel_length_m and
                call.draft_m <= config.max_vessel_draft_m)

    def batches(call: VesselCall, flow: str) -> list[dict[str, Any]]:
        nonlocal batch_count
        count = call.import_boxes if flow == "import" else call.export_boxes
        jobs = []
        for offset in range(0, count, batch_size):
            amount = min(batch_size, count - offset)
            jobs.append({"vessel_id": call.vessel_id, "flow": flow,
                         "box_id": f"{call.vessel_id}:{flow}:{offset}", "batch_start": offset,
                         "count": amount, "aggregated": amount > 1,
                         "prestage_tokens": 0,
                         "yard_ready": env.event(), "apron_ready": env.event()})
            batch_count += 1
        return jobs

    def job_fields(job: dict, berth: int | None = None) -> dict[str, Any]:
        fields = {key: job[key] for key in
                  ("vessel_id", "flow", "box_id", "count", "aggregated")}
        if berth is not None:
            fields["berth"] = berth
        return fields

    def release_import(job: dict):
        nonlocal yard_physical
        yield env.timeout(config.import_dwell_hours)
        amount = job["count"]
        inventory["import_yard"] -= amount
        inventory["import_released"] += amount
        yard_physical -= amount
        yield yard_slots.put(amount)
        wake_cargo()
        emit("yard_out", **job_fields(job), reason="import_dwell_complete",
             yard_boxes=yard_physical)

    def release_initial():
        nonlocal yard_physical
        yield env.timeout(config.import_dwell_hours)
        amount = config.initial_yard_boxes
        inventory["initial_yard"] -= amount
        inventory["initial_released"] += amount
        yard_physical -= amount
        yield yard_slots.put(amount)
        wake_cargo()
        emit("yard_out", count=amount, flow="initial", box_id="initial_inventory",
             reason="initial_inventory_dwell_complete", yard_boxes=yard_physical)

    def stage_exports(call: VesselCall, jobs: list[dict]):
        nonlocal yard_physical, peak_yard
        yield env.timeout(max(0.0, call.arrival_hour - config.export_lead_hours))
        for job in jobs:
            amount = job["count"]
            if not berth_ready[call.vessel_id].triggered:
                budget_request = prestage_slots.get(amount)
                ready = yield env.any_of([budget_request, berth_ready[call.vessel_id]])
                if budget_request.triggered:
                    if berth_ready[call.vessel_id].triggered:
                        yield prestage_slots.put(amount)
                    else:
                        job["prestage_tokens"] = amount
                else:
                    budget_request.cancel()
            yield yard_slots.get(amount)
            prefix = "future_export" if call.vessel_id in future_staging_ids else "export"
            inventory[prefix + "_not_staged"] -= amount
            inventory[prefix + "_yard"] += amount
            yard_physical += amount
            peak_yard = max(peak_yard, yard_physical)
            emit("yard_in", **job_fields(job), reason="export_gate_staging",
                 yard_boxes=yard_physical)
            job["yard_ready"].succeed()

    def transport_export(job: dict, berth: int):
        nonlocal yard_physical
        amount = job["count"]
        yield job["yard_ready"]
        yield apron_slots.get(amount)
        inventory["export_reserved_apron"] += amount
        tractor_id = yield tractor_pool.get()
        inventory["export_yard"] -= amount
        inventory["export_on_tractor"] += amount
        yard_physical -= amount
        yield yard_slots.put(amount)
        wake_cargo()
        emit("yard_out", **job_fields(job, berth), reason="export_loading",
             yard_boxes=yard_physical)
        start = env.now
        duration = amount * config.tractor_cycle_minutes / 60.0 * _factor(
            config.seed, job["vessel_id"], "export", job["batch_start"], "tractor",
            config.service_variability)
        end = start + duration
        fields = {**job_fields(job, berth), "tractor_id": tractor_id,
                  "start_time": float(start), "end_time": float(end)}
        emit("tractor_start", **fields)
        interval("tractor", tractor_id, start, end, **job_fields(job, berth))
        yield env.timeout(duration)
        inventory["export_on_tractor"] -= amount
        inventory["export_reserved_apron"] -= amount
        inventory["export_apron"] += amount
        emit("tractor_end", **fields)
        yield tractor_pool.put(tractor_id)
        job["apron_ready"].succeed()
        wake_cargo()

    def transport_import(job: dict, berth: int):
        nonlocal yard_physical, peak_yard
        amount = job["count"]
        tractor_id = yield tractor_pool.get()
        inventory["import_apron"] -= amount
        inventory["import_on_tractor"] += amount
        yield apron_slots.put(amount)
        wake_cargo()
        start = env.now
        duration = amount * config.tractor_cycle_minutes / 60.0 * _factor(
            config.seed, job["vessel_id"], "import", job["batch_start"], "tractor",
            config.service_variability)
        end = start + duration
        fields = {**job_fields(job, berth), "tractor_id": tractor_id,
                  "start_time": float(start), "end_time": float(end)}
        emit("tractor_start", **fields)
        interval("tractor", tractor_id, start, end, **job_fields(job, berth))
        yield env.timeout(duration)
        inventory["import_on_tractor"] -= amount
        inventory["import_reserved_yard"] -= amount
        inventory["import_yard"] += amount
        yard_physical += amount
        peak_yard = max(peak_yard, yard_physical)
        rows[job["vessel_id"]]["import_delivered_boxes"] += amount
        emit("tractor_end", **fields)
        emit("yard_in", **job_fields(job, berth), reason="import_delivery",
             yard_boxes=yard_physical)
        yield tractor_pool.put(tractor_id)
        env.process(release_import(job))

    def crane_lane(pending_imports: deque, pending_exports: list, berth: int,
                   import_transports: list):
        while pending_imports or pending_exports:
            job = None
            reservations = []
            if pending_imports:
                amount = pending_imports[0]["count"]
                # Reserve both spaces in the same event callback. Waiting on
                # one slot after holding the other can strand every crane
                # lane while ready exports occupy the apron. Respect older
                # Container requests rather than jumping their FIFO queue.
                if (yard_slots.level >= amount and apron_slots.level >= amount and
                        not yard_slots.get_queue and not apron_slots.get_queue):
                    job = pending_imports.popleft()
                    reservations = [yard_slots.get(amount), apron_slots.get(amount)]
                    inventory["import_reserved_yard"] += amount
                    inventory["import_reserved_apron"] += amount
            if job is None:
                for index, export in enumerate(pending_exports):
                    if export["apron_ready"].triggered:
                        job = pending_exports.pop(index)
                        break
            if job is None:
                yield cargo_changed
                continue
            amount = job["count"]
            flow = job["flow"]
            if reservations:
                yield env.all_of(reservations)
            crane_id = yield crane_pool.get()
            start = env.now
            duration = amount / config.crane_moves_per_hour * _factor(
                config.seed, job["vessel_id"], flow, job["batch_start"], "crane",
                config.service_variability)
            end = start + duration
            fields = {**job_fields(job, berth), "crane_id": crane_id,
                      "start_time": float(start), "end_time": float(end)}
            emit("crane_start", **fields)
            interval("crane", crane_id, start, end, **job_fields(job, berth))
            yield env.timeout(duration)
            if flow == "import":
                inventory["import_on_vessel"] -= amount
                inventory["import_reserved_apron"] -= amount
                inventory["import_apron"] += amount
                rows[job["vessel_id"]]["import_discharged_boxes"] += amount
                import_transports.append(env.process(transport_import(job, berth)))
            else:
                inventory["export_apron"] -= amount
                inventory["export_loaded"] += amount
                rows[job["vessel_id"]]["export_loaded_boxes"] += amount
                yield apron_slots.put(amount)
                wake_cargo()
            emit("crane_end", **fields)
            yield crane_pool.put(crane_id)

    def vessel_process(call: VesselCall, import_jobs: list, export_jobs: list):
        nonlocal completed_count
        row = rows[call.vessel_id]
        yield env.timeout(call.arrival_hour)
        row["status"] = "waiting"
        emit("arrival", vessel_id=call.vessel_id, import_boxes=call.import_boxes,
             export_boxes=call.export_boxes)
        if not compatible(call):
            row["status"] = "incompatible"
            incompatible_ids.add(call.vessel_id)
            emit("incompatible", vessel_id=call.vessel_id,
                 length_m=call.length_m, draft_m=call.draft_m)
            return
        waiting.add(call.vessel_id)
        queue_changed()
        berth = yield berth_pool.get()
        waiting.remove(call.vessel_id)
        working.add(call.vessel_id)
        queue_changed()
        row["berth_start_hour"] = float(env.now)
        row["berth"] = berth
        row["status"] = "in_service"
        berth_ready[call.vessel_id].succeed()
        for job in export_jobs:
            if job["prestage_tokens"]:
                yield prestage_slots.put(job["prestage_tokens"])
                job["prestage_tokens"] = 0
        emit("berth_start", vessel_id=call.vessel_id, berth=berth)
        berth_interval = {"resource": "berth", "resource_id": berth, "berth": berth,
                          "vessel_id": call.vessel_id, "start_time": float(env.now),
                          "end_time": None}
        intervals.append(berth_interval)
        for job in export_jobs:
            env.process(transport_export(job, berth))
        pending_imports = deque(import_jobs)
        pending_exports = list(export_jobs)
        import_transports = []
        lanes = [env.process(crane_lane(pending_imports, pending_exports, berth, import_transports))
                 for _ in range(min(config.cranes_per_vessel, max(1, len(import_jobs) + len(export_jobs))))]
        yield env.all_of(lanes)
        if import_transports:
            yield env.all_of(import_transports)
        row["departure_hour"] = float(env.now)
        row["status"] = "served"
        completed_count += 1
        working.remove(call.vessel_id)
        berth_interval["end_time"] = float(env.now)
        emit("departure", vessel_id=call.vessel_id, berth=berth)
        yield berth_pool.put(berth)

    def state_snapshot(hour: float) -> dict[str, Any]:
        return {"time": float(hour), "queued_calls": len(waiting),
                            "working_calls": len(working), "served_calls": completed_count,
                            "yard_stock_boxes": yard_physical,
                            "future_export_yard_boxes": inventory["future_export_yard"],
                            "yard_fill_fraction": yard_physical / config.yard_capacity_boxes,
                            "import_on_vessel_boxes": inventory["import_on_vessel"],
                            "export_unloaded_boxes": (inventory["export_not_staged"] +
                                                       inventory["export_yard"] +
                                                       inventory["export_on_tractor"] +
                                                       inventory["export_apron"]),
                            "throughput_boxes": inventory["import_apron"] + inventory["import_on_tractor"] +
                                                inventory["import_yard"] + inventory["import_released"] +
                                                inventory["export_loaded"],
                            "pressure_raw": gate.raw_pressure,
                            "pressure_eligible": gate.eligible(hour)}

    def sample_state():
        sample_interval = max(1.0, config.horizon_hours / 2048.0)
        while True:
            gate.advance(env.now)
            samples.append(state_snapshot(env.now))
            yield env.timeout(sample_interval)

    if config.initial_yard_boxes:
        env.process(release_initial())
    for call in selected:
        rows[call.vessel_id] = {"vessel_id": call.vessel_id,
                              "arrival_hour": float(call.arrival_hour),
                              "length_m": call.length_m, "draft_m": call.draft_m,
                              "box40_share": call.box40_share, "port": call.port,
                              "berth_start_hour": None, "departure_hour": None,
                              "wait_hours": None, "import_boxes": call.import_boxes,
                              "export_boxes": call.export_boxes, "status": "not_arrived",
                              "import_discharged_boxes": 0, "import_delivered_boxes": 0,
                              "export_loaded_boxes": 0, "evidence_type": call.evidence_type,
                              "observed_berth_start_hour": getattr(call, "observed_berth_start_hour", None),
                              "observed_departure_hour": getattr(call, "observed_departure_hour", None),
                              "wait_censored": False, "turnaround_censored": True}
        import_jobs = batches(call, "import")
        export_jobs = batches(call, "export")
        if compatible(call) and export_jobs:
            env.process(stage_exports(call, export_jobs))
        env.process(vessel_process(call, import_jobs, export_jobs))
    for call in future_staging_calls:
        env.process(stage_exports(call, batches(call, "export")))
    env.process(sample_state())
    was_cancelled = False
    next_progress = 0.0
    _notify(progress, 0.0, f"Simulating {config.port}: {len(selected)} scheduled calls")
    while env.peek() <= config.horizon_hours:
        if _cancelled(cancel):
            was_cancelled = True
            break
        # Complete the current timestamp before honouring cancellation. This
        # prevents accepted resource requests awaiting same-time resumption
        # from leaving counts inconsistent in the returned partial result.
        timestamp = env.peek()
        while env.peek() <= timestamp:
            env.step()
        if env.now >= next_progress:
            _notify(progress, min(0.99, env.now / config.horizon_hours),
                    f"Hour {env.now:.1f}: {completed_count} calls served; {len(waiting)} waiting")
            next_progress = env.now + max(1.0, config.horizon_hours / 100.0)
    stopped_at = float(env.now) if was_cancelled else float(config.horizon_hours)
    gate.advance(stopped_at)
    # Periodic monitoring may execute before cargo/departure events with the
    # same timestamp. Finish its series with the settled cutoff state.
    final_sample = state_snapshot(stopped_at)
    if samples and abs(samples[-1]["time"] - stopped_at) < 1e-9:
        samples[-1] = final_sample
    else:
        samples.append(final_sample)
    waits = []
    completed_waits = []
    turnarounds = []
    restricted_turnarounds = []
    vessels = list(rows.values())
    for row in vessels:
        if row["status"] not in ("incompatible", "not_arrived"):
            wait_end = row["berth_start_hour"] if row["berth_start_hour"] is not None else stopped_at
            row["wait_hours"] = max(0.0, wait_end - row["arrival_hour"])
            row["wait_censored"] = row["berth_start_hour"] is None
            waits.append(row["wait_hours"])
            if not row["wait_censored"]:
                completed_waits.append(row["wait_hours"])
            turnaround_end = row["departure_hour"] if row["departure_hour"] is not None else stopped_at
            restricted_turnarounds.append(max(0.0, turnaround_end - row["arrival_hour"]))
        row["turnaround_censored"] = row["departure_hour"] is None
        if row["departure_hour"] is not None:
            turnarounds.append(row["departure_hour"] - row["arrival_hour"])
    for item in intervals:
        planned_end = item["end_time"]
        item["planned_end_time"] = planned_end
        item["censored"] = planned_end is None or planned_end > stopped_at
        item["end_time"] = min(stopped_at, planned_end if planned_end is not None else stopped_at)
    busy = {name: sum(max(0.0, item["end_time"] - item["start_time"])
                     for item in intervals if item["resource"] == name)
            for name in ("berth", "crane", "tractor")}
    resource_overlaps = []
    for resource in busy:
        for resource_id in range(getattr(config, resource + "s")):
            uses = sorted((item for item in intervals if item["resource"] == resource and
                           item["resource_id"] == resource_id), key=lambda item: item["start_time"])
            for previous, current in zip(uses, uses[1:]):
                if previous["end_time"] > current["start_time"] + 1e-8:
                    resource_overlaps.append({"resource": resource, "resource_id": resource_id,
                                              "at_hour": current["start_time"]})
    import_total = sum(call.import_boxes for call in selected)
    export_total = sum(call.export_boxes for call in selected)
    import_accounted = sum(inventory[key] for key in
                           ("import_on_vessel", "import_apron", "import_on_tractor", "import_yard", "import_released"))
    export_accounted = sum(inventory[key] for key in
                           ("export_not_staged", "export_yard", "export_on_tractor", "export_apron", "export_loaded"))
    yard_accounted = (inventory["import_yard"] + inventory["export_yard"] +
                      inventory["future_export_yard"] + inventory["initial_yard"])
    checks = {"cancelled": was_cancelled,
              "import_conserved": import_accounted == import_total,
              "export_conserved": export_accounted == export_total,
              "future_export_conserved": inventory["future_export_not_staged"] + inventory["future_export_yard"] ==
                                           sum(call.export_boxes for call in future_staging_calls),
              "initial_inventory_conserved": inventory["initial_yard"] + inventory["initial_released"] == config.initial_yard_boxes,
              "yard_inventory_consistent": yard_accounted == yard_physical,
              "yard_capacity_respected": 0 <= peak_yard <= config.yard_capacity_boxes,
              "yard_slot_balance": abs(yard_slots.level + yard_physical + inventory["import_reserved_yard"] - config.yard_capacity_boxes) < 1e-8,
              "apron_slot_balance": abs(apron_slots.level + inventory["import_reserved_apron"] +
                                          inventory["import_apron"] + inventory["export_reserved_apron"] +
                                          inventory["export_apron"] - apron_capacity) < 1e-8,
              "apron_capacity_respected": 0 <= apron_slots.level <= apron_capacity,
              "resource_bounds_respected": not resource_overlaps and all(
                  busy[name] <= stopped_at * getattr(config, name + "s") + 1e-7 for name in busy),
              "resource_overlaps": resource_overlaps,
              "inventory_nonnegative": all(value >= 0 for value in inventory.values()),
              "inventory": inventory.copy(), "requested_import_boxes": import_total,
              "requested_export_boxes": export_total,
              "future_export_appointment_boxes": sum(call.export_boxes for call in future_staging_calls),
              "future_export_appointment_vessel_ids": sorted(future_staging_ids),
              "censored_vessel_ids": [row["vessel_id"] for row in vessels if row["turnaround_censored"] and row["status"] != "incompatible"],
              "incompatible_vessel_ids": sorted(incompatible_ids),
              "pressure_episodes": gate.episodes,
              "trace_captured": bool(capture_events),
              "trace_complete": bool(capture_events) and trace_skipped == 0,
              "trace_cutoff_hour": (float(events[-1]["time"]) if trace_skipped and events else stopped_at)
                                   if capture_events else None,
              "trace_events_omitted": trace_skipped,
              "aggregation_batch_boxes": batch_size, "handling_batches": batch_count,
              "aggregation_basis": "Entire uploaded port trace, before horizon filtering",
              "handling_job_limit": MAX_HANDLING_BATCHES,
              "apron_capacity_boxes": apron_capacity,
              "waiting_export_prestage_capacity_boxes": prestage_capacity,
              "cargo_dispatch_policy": "Import-preferred feasible interleaving; ready exports load when discharge lacks joint yard/apron space",
              "sd_model": "Measured stock/flow exposure; no calibrated endogenous demand or congestion feedback"}
    throughput = import_total - inventory["import_on_vessel"] + inventory["export_loaded"]
    service_backlog_boxes = (inventory["import_on_vessel"] + inventory["import_apron"] +
                             inventory["import_on_tractor"] + inventory["export_not_staged"] +
                             inventory["export_yard"] + inventory["export_on_tractor"] +
                             inventory["export_apron"])
    kpis = {"requested_calls": len(selected), "served_calls": completed_count,
            "unserved_calls": len(selected) - completed_count,
            "incompatible_calls": len(incompatible_ids), "end_backlog_calls": len(waiting) + len(working),
            "waiting_calls_at_end": len(waiting), "working_calls_at_end": len(working),
            "mean_wait_hours": statistics.fmean(waits) if waits else None,
            "restricted_mean_wait_hours": statistics.fmean(waits) if waits else None,
            "completed_mean_wait_hours": statistics.fmean(completed_waits) if completed_waits else None,
            "wait_population_calls": len(waits), "completed_wait_calls": len(completed_waits),
            "total_observed_wait_hours": sum(waits),
            "wait_measure": "Observed wait through cutoff; unfinished waits are lower bounds, not estimates of eventual wait",
            "p95_wait_hours": _percentile(waits, .95) if waits else None,
            "waits_censored": sum(row["wait_censored"] for row in vessels),
            "mean_turnaround_hours": statistics.fmean(turnarounds) if turnarounds else None,
            "restricted_mean_turnaround_hours": statistics.fmean(restricted_turnarounds) if restricted_turnarounds else None,
            "turnaround_observed_calls": len(turnarounds), "throughput_boxes": throughput,
            "service_backlog_boxes": service_backlog_boxes,
            "future_export_staged_boxes": inventory["future_export_yard"],
            "berth_utilization": busy["berth"] / (stopped_at * config.berths) if stopped_at else 0.0,
            "crane_utilization": busy["crane"] / (stopped_at * config.cranes) if stopped_at else 0.0,
            "tractor_utilization": busy["tractor"] / (stopped_at * config.tractors) if stopped_at else 0.0,
            "peak_yard_boxes": peak_yard, "ending_yard_boxes": yard_physical,
            "gate_status": gate.status(stopped_at),
            "eligible_hour": gate.target_hour() if gate.eligible(stopped_at) else None,
            "first_eligible_hour": gate.first_eligible_hour,
            "pressure_exposure_hours": gate.pressure_exposure_hours,
            "eligible_exposure_hours": gate.eligible_exposure_hours,
            "queue_peak_calls": queue_peak, "simulated_hours": stopped_at,
            "wall_seconds": time.perf_counter() - wall_start,
            "aggregation_batch_boxes": batch_size, "stock_flow_samples": samples}
    kpis["operational_prediction_diagnostics"] = _operational_diagnostics(vessels)
    notes = ["Conditional scenario simulation with illustrative resource/timing settings; no claim of calibrated terminal prediction.",
             "Compatible vessels use identical berth length/draft limits; crane and tractor fleets are shared.",
             "Export cargo waits outside the gate until finite yard space exists. Import yard space is reserved before discharge.",
             "Known future vessel appointments may pre-stage exports before the cutoff even when their arrival falls after it; their yard stock is accounted separately and those calls do not enter service/backlog counts.",
             "A conservative gate-admission policy collectively limits export pre-staging for unberthed vessels to half the yard, reserving the remainder for working vessels; this operational policy needs confirmation.",
             "Crane dispatch prefers import discharge when both yard and apron space are available; it loads ready exports to release space otherwise. This feasible aggregate interleaving does not model hatch sequencing, stow plans, or shipboard stability constraints.",
             f"Apron space is a derived prototype bound of {apron_capacity} boxes; initial yard stock releases after the configured import dwell.",
             "Waiting statistics include unfinished waits truncated at the observation horizon; completed-only turnaround statistics are reported separately.",
             "Stock/flow monitoring describes the DES; it does not invent calibrated SD feedback or financial ROI."]
    notes.append(kpis["operational_prediction_diagnostics"]["interpretation"])
    if batch_size > 1:
        notes.append(f"Handling is aggregated in jobs of up to {batch_size} boxes, resolved from the entire uploaded port trace so horizon changes preserve earlier trajectories. Counts are exact; within-job box timing and parallelism are approximated.")
    if trace_skipped:
        notes.append(f"Visual trace limit reached: {trace_skipped} events omitted; numerical counts and utilization remain complete.")
    if any(call.evidence_type.casefold() in ("synthetic", "example", "unverified") for call in selected):
        notes.append("Some or all uploaded call records are synthetic/unverified; simulated outputs do not validate actual port performance.")
    _notify(progress, 1.0, "Simulation cancelled" if was_cancelled else "Simulation complete")
    return SimulationResult(config=replace(config), vessels=vessels, intervals=intervals,
                            events=events, kpis=kpis, checks=checks, evidence_notes=notes)


def _summary(result: SimulationResult) -> dict[str, Any]:
    return {key: value for key, value in result.kpis.items() if key != "stock_flow_samples"}


def _operational_diagnostics(vessels: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare supplied observed times to this run without fitting parameters."""
    groups = {}
    nonobserved_supplied = 0
    for row in vessels:
        row["berth_start_error_hours"] = None
        row["departure_error_hours"] = None
        row["observed_wait_hours"] = None
        row["observed_turnaround_hours"] = None
        if str(row["evidence_type"]).casefold() != "observed":
            nonobserved_supplied += int(row.get("observed_berth_start_hour") is not None or
                                       row.get("observed_departure_hour") is not None)
    for label, actual_field, predicted_field, error_field, duration_field in (
        ("berth_start", "observed_berth_start_hour", "berth_start_hour", "berth_start_error_hours", "observed_wait_hours"),
        ("departure", "observed_departure_hour", "departure_hour", "departure_error_hours", "observed_turnaround_hours"),
    ):
        errors = []
        observed = 0
        for row in vessels:
            actual = row.get(actual_field)
            if actual is None or str(row["evidence_type"]).casefold() != "observed":
                continue
            observed += 1
            row[duration_field] = actual - row["arrival_hour"]
            predicted = row[predicted_field]
            if predicted is not None:
                error = predicted - actual
                errors.append(error)
                row[error_field] = error
        groups[label] = {"population_calls": len(vessels), "observed_calls": observed,
                         "paired_calls": len(errors), "missing_observation_calls": len(vessels) - observed,
                         "missing_prediction_or_censored_calls": observed - len(errors),
                         "pair_fraction_of_observed": len(errors) / observed if observed else None,
                         "mae_hours": statistics.fmean(abs(error) for error in errors) if errors else None,
                         "rmse_hours": math.sqrt(statistics.fmean(error * error for error in errors)) if errors else None,
                         "bias_hours": statistics.fmean(errors) if errors else None}
    paired = sum(group["paired_calls"] for group in groups.values())
    observed = sum(group["observed_calls"] for group in groups.values())
    status = ("observations_compared_not_independently_validated" if paired else
              "observations_available_no_complete_pairs" if observed else "not_validated")
    interpretation = ("Operational timing errors are same-run hindcast diagnostics against supplied observed calls; "
                      "they do not establish independent predictive validation or calibration. Missing/censored pairs are reported.")
    if not observed:
        interpretation = ("No observed operational timing records were supplied for this run. Vessel waiting/turnaround predictions "
                          "remain unvalidated conditional simulation outputs; synthetic schedules do not validate them.")
    return {"status": status, "scope": "Same-run hindcast; no independent holdout and no parameter fitting",
            "error_sign": "predicted minus supplied observed hours", "interpretation": interpretation,
            "nonobserved_calls_with_supplied_times_ignored": nonobserved_supplied, **groups}


def _physical_failures(result: SimulationResult) -> list[str]:
    return [name for name in REQUIRED_PHYSICAL_CHECKS if result.checks.get(name) is not True]


def _confidence_details(family_size: int, degrees_freedom: int) -> dict[str, Any]:
    """Conservative Bonferroni guard using the primary NIST Student-t table.

    For three/four comparisons, use the next more conservative available
    one-sided tail (0.01) instead of interpolating a critical value. The model
    has at most five predeclared candidate alternatives.
    """
    if not 1 <= family_size <= 5:
        raise ValueError("Student-t table guard supports one through five predeclared candidates")
    # Columns 0.95, 0.975 and 0.99, df1..30, from NIST/SEMATECH e-Handbook.
    tables = {
        .05: [6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812,
              1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725,
              1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697],
        .025: [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
               2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
               2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042],
        .01: [31.821, 6.965, 4.541, 3.747, 3.365, 3.143, 2.998, 2.896, 2.821, 2.764,
              2.718, 2.681, 2.650, 2.624, 2.602, 2.583, 2.567, 2.552, 2.539, 2.528,
              2.518, 2.508, 2.500, 2.492, 2.485, 2.479, 2.473, 2.467, 2.462, 2.457],
    }
    used_alpha = .05 if family_size == 1 else .025 if family_size == 2 else .01
    df_used = min(degrees_freedom, 30)
    if df_used < 1:
        raise ValueError("At least one degree of freedom is required")
    # NIST displays three decimals. Add .001 so tabular rounding cannot make
    # the selected critical value less conservative than its published value.
    critical = tables[used_alpha][df_used - 1] + .001
    return {"method": "Bonferroni family-wise one-sided Student-t guard",
            "predeclared_candidates": family_size, "target_family_confidence": .95,
            "requested_one_sided_alpha": .05 / family_size, "used_one_sided_alpha": used_alpha,
            "degrees_freedom": degrees_freedom, "table_degrees_freedom": df_used,
            "critical_value": critical, "table_probability": 1 - used_alpha,
            "table_rounding_guard": .001,
            "assumptions": "Paired replication means are treated as approximately normal; small replication counts do not cover model/evidence uncertainty.",
            "source": "https://www.itl.nist.gov/div898/handbook/eda/section3/eda3672.htm",
            "adjustment_source": "https://www.itl.nist.gov/div898/handbook/prc/section4/prc463.htm"}


def _lower_bound(values: list[float], family_size: int = 1) -> float:
    """Family-adjusted lower bound of paired mean observed wait reduction."""
    if len(values) < 2:
        return float("-inf")
    mean = statistics.fmean(values)
    critical = _confidence_details(family_size, len(values) - 1)["critical_value"]
    return mean - critical * statistics.stdev(values) / math.sqrt(len(values))


def compare_candidates(calls: list[VesselCall], config: TerminalConfig,
                       progress: Callable | None = None, cancel: Any = None) -> dict[str, Any]:
    """Fresh paired retrospective scenarios; never an investment/ROI claim."""
    config.validate()
    baseline_gate = run_simulation(calls, config, capture_events=False, cancel=cancel)
    common = {"baseline": _summary(baseline_gate), "candidates": [],
              "comparison_scope": "Retrospective full-horizon configuration counterfactuals; changes assumed present from hour zero, not purchases at the decision epoch.",
              "uncertainty": "Bonferroni family-adjusted one-sided Student-t lower bounds across all predeclared candidates, using conservative NIST tabulated tails. Paired replication means are treated as approximately normal. Simulation uncertainty only; small replication counts do not cover model/evidence uncertainty.",
              "evidence_note": "Conditional operational comparison. No calibrated causal benefit, investment cost, or ROI is inferred."}
    if baseline_gate.checks["cancelled"]:
        return {**common, "status": "cancelled", "reason": "Comparison cancelled"}
    baseline_failures = _physical_failures(baseline_gate)
    if baseline_failures:
        return {**common, "status": "invalid_baseline", "baseline_check_failures": baseline_failures,
                "reason": "The baseline failed required physical/accounting checks; no recommendation can be supported."}
    if baseline_gate.kpis["gate_status"] != "eligible":
        return {**common, "status": "gate_not_met",
                "reason": "Current raw queue pressure has not passed both observation and uninterrupted persistence, or it cleared before the horizon; no active recommendation."}
    if config.replications < 2:
        return {**common, "status": "insufficient_replications",
                "reason": "At least two paired replications are needed for an uncertainty lower bound."}
    definitions = []
    if config.import_dwell_hours > 0:
        definitions.append(("Earlier import release", "operational",
                            {"import_dwell_hours": config.import_dwell_hours * .75},
                            "Requires evidence that release/clearance can shorten dwell by 25%."))
    definitions.append(("Faster tractor dispatch", "operational",
                        {"tractor_cycle_minutes": config.tractor_cycle_minutes * .8},
                        "Requires an attainable 20% reduction in the measured tractor cycle."))
    if config.cranes_per_vessel < config.cranes:
        definitions.append(("Reassign existing cranes", "operational",
                            {"cranes_per_vessel": config.cranes_per_vessel + 1},
                            "Uses the same crane fleet with one higher per-vessel assignment limit."))
    definitions.extend([("One additional crane", "capacity", {"cranes": config.cranes + 1},
                         "Hypothetical crane available for the entire run; feasibility and cost are unknown."),
                        ("One additional compatible berth", "capacity", {"berths": config.berths + 1},
                         "Hypothetical berth with the same limits available for the entire run; no construction or purchase is simulated.")])
    confidence = _confidence_details(len(definitions), config.replications - 1)
    common["confidence_adjustment"] = confidence
    paired_baselines = []
    total_runs = config.replications * (len(definitions) + 1)
    finished = 0
    for replication in range(config.replications):
        if _cancelled(cancel):
            return {**common, "status": "cancelled", "reason": "Comparison cancelled"}
        paired_config = replace(config, seed=config.seed + replication * 1009)
        paired_baselines.append(run_simulation(calls, paired_config, False, cancel=cancel))
        finished += 1
        _notify(progress, finished / total_runs, f"Baseline replication {replication + 1}/{config.replications}")
    for name, category, changes, assumption in definitions:
        reductions = []
        paired_rows = []
        count_violations = []
        physical_violations = []
        for replication, baseline in enumerate(paired_baselines):
            if _cancelled(cancel):
                return {**common, "status": "cancelled", "reason": "Comparison cancelled"}
            candidate = run_simulation(calls, replace(config, seed=config.seed + replication * 1009,
                                                      **changes), False, cancel=cancel)
            if candidate.checks["cancelled"]:
                return {**common, "status": "cancelled", "reason": "Comparison cancelled"}
            reduction = baseline.kpis["mean_wait_hours"] - candidate.kpis["mean_wait_hours"]
            reductions.append(reduction)
            pair = {"replication": replication + 1, "seed": config.seed + replication * 1009,
                    "baseline_wait_hours": baseline.kpis["mean_wait_hours"],
                    "candidate_wait_hours": candidate.kpis["mean_wait_hours"],
                    "wait_reduction_hours": reduction,
                    "served_delta": candidate.kpis["served_calls"] - baseline.kpis["served_calls"],
                    "backlog_delta": candidate.kpis["end_backlog_calls"] - baseline.kpis["end_backlog_calls"],
                    "unserved_delta": candidate.kpis["unserved_calls"] - baseline.kpis["unserved_calls"],
                    "incompatible_delta": candidate.kpis["incompatible_calls"] - baseline.kpis["incompatible_calls"]}
            pair["baseline_check_failures"] = _physical_failures(baseline)
            pair["candidate_check_failures"] = _physical_failures(candidate)
            baseline_cargo = baseline.kpis.get("service_backlog_boxes")
            candidate_cargo = candidate.kpis.get("service_backlog_boxes")
            pair["service_backlog_boxes_delta"] = (candidate_cargo - baseline_cargo
                                                  if baseline_cargo is not None and candidate_cargo is not None else None)
            if baseline_cargo is None:
                pair["baseline_check_failures"].append("service_backlog_measure_missing")
            if candidate_cargo is None:
                pair["candidate_check_failures"].append("service_backlog_measure_missing")
            if pair["baseline_check_failures"] or pair["candidate_check_failures"]:
                physical_violations.append(replication + 1)
            paired_rows.append(pair)
            if (any(pair[key] > 0 for key in ("backlog_delta", "unserved_delta", "incompatible_delta")) or
                    (pair["service_backlog_boxes_delta"] is not None and pair["service_backlog_boxes_delta"] > 0)):
                count_violations.append(replication + 1)
            finished += 1
            _notify(progress, finished / total_runs, f"{name}: replication {replication + 1}/{config.replications}")
        lower = _lower_bound(reductions, family_size=len(definitions))
        accepted = lower > 0 and not count_violations and not physical_violations
        if physical_violations:
            reason = f"Rejected: required physical/accounting checks failed in paired replications {physical_violations}."
        elif count_violations:
            reason = f"Rejected: incompatible/unserved/end-backlog calls or unfinished cargo boxes increased in paired replications {count_violations}."
        elif lower <= 0:
            reason = "No positive conservative paired wait-reduction lower bound."
        else:
            reason = "Positive family-adjusted paired wait-reduction lower bound with no worsening call/cargo constraints; conditional scenario only."
        common["candidates"].append({"name": name, "category": category, "changes": changes,
                                     "assumption": assumption,
                                     "mean_wait_reduction_hours": statistics.fmean(reductions),
                                     "lower_bound_hours": lower,
                                     "served_delta": statistics.fmean(row["served_delta"] for row in paired_rows),
                                     "backlog_delta": statistics.fmean(row["backlog_delta"] for row in paired_rows),
                                     "unserved_delta": statistics.fmean(row["unserved_delta"] for row in paired_rows),
                                     "incompatible_delta": statistics.fmean(row["incompatible_delta"] for row in paired_rows),
                                     "service_backlog_boxes_delta": statistics.fmean(row["service_backlog_boxes_delta"] for row in paired_rows)
                                                                    if all(row["service_backlog_boxes_delta"] is not None for row in paired_rows) else None,
                                     "accepted": accepted, "reason": reason,
                                     "confidence_adjustment": confidence,
                                     "physical_check_failed_replications": physical_violations,
                                     "paired_replications": len(paired_rows), "paired_results": paired_rows})
    operational = [row for row in common["candidates"] if row["category"] == "operational" and row["accepted"]]
    accepted = operational or [row for row in common["candidates"] if row["accepted"]]
    preferred = max(accepted, key=lambda row: row["lower_bound_hours"])["name"] if accepted else None
    _notify(progress, 1.0, "Conditional scenario comparison complete")
    return {**common, "status": "evaluated", "preferred_candidate": preferred,
            "reason": "Existing-resource operational scenarios were considered before added capacity. Acceptance requires all physical/accounting checks, a positive family-adjusted uncertainty lower bound and no increased incompatible/unserved/end-backlog calls or unfinished cargo boxes in any pair."}
