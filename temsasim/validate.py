"""Model-to-measurement correlation.

This module is how a component's ``validated: false`` becomes ``true``, and it
is the only legitimate way. Everything else in the package is physics; this is
the evidence that the physics describes *your* bus.

Feed it a measured log from a real vehicle -- the speed it actually drove, the
gradient, occupancy and door state if you have them -- and it replays exactly
that trace through the model, then compares what the model says the battery
did against what the battery actually did.

The metrics are chosen for what they catch:

``energy error``
    Total battery energy, simulated against measured. The headline number and
    the one that decides range. Target within +/-5 % for a tender-grade model.
    Errors here can hide compensating mistakes (rolling resistance too high,
    HVAC too low), which is why it is never the only check.

``SOC trajectory RMSE``
    How far the simulated state of charge wanders from the logged one over the
    run. Catches a model that gets the total right by accident while being wrong
    all the way through.

``power correlation and time-resolved error``
    Instant by instant. A high correlation with a steady offset points at a
    constant load (auxiliaries, HVAC); a poor correlation points at dynamics
    (mass, inertia, motor map, regen limits).

``split by phase``
    Energy error separately for driving, braking and standstill. This is the
    diagnostic step: a large standstill error is an auxiliary or climate
    problem, a large braking error is a regen-limit problem, and a large
    driving error is road load or the motor map. Without this split a
    validation failure tells you *that* the model is wrong but not *where*.
"""

from __future__ import annotations

import csv
import pathlib
from dataclasses import dataclass, field

import numpy as np

from .cycle import DriveCycle, Environment
from .messages import Note, tr_num
from .simulator import SimulationResult, simulate
from .units import KWH
from .vehicle import Vehicle


@dataclass
class MeasuredLog:
    """A measured drive, as exported from vehicle CAN / telematics."""

    time_s: np.ndarray
    speed_mps: np.ndarray
    battery_power_w: np.ndarray | None = None
    soc: np.ndarray | None = None
    grade_rad: np.ndarray | None = None
    passengers: np.ndarray | None = None
    doors_open: np.ndarray | None = None
    ambient_c: float | None = None
    name: str = "measured"
    source: str = "unspecified"

    @classmethod
    def from_csv(cls, path) -> "MeasuredLog":
        """Read a log CSV.

        Required: ``time_s`` and one of ``speed_mps`` / ``speed_kmh``.
        Needed to validate anything: ``battery_power_kw`` (positive discharging)
        and/or ``soc_percent``.
        Optional but strongly recommended: ``grade_percent``, ``passengers``,
        ``doors_open``, ``ambient_c``. Without gradient, a hilly route will
        validate badly for reasons that have nothing to do with the bus.
        """
        path = pathlib.Path(path)
        with path.open(newline="") as fh:
            rows = [r for r in csv.DictReader(
                line for line in fh if not line.lstrip().startswith("#"))]
        if not rows:
            raise ValueError(f"{path}: no data rows")

        def col(name):
            if name not in rows[0] or rows[0][name] in (None, ""):
                return None
            return np.array([float(r[name]) for r in rows])

        t = col("time_s")
        if t is None:
            raise ValueError(f"{path}: missing time_s")
        v = col("speed_mps")
        if v is None:
            kmh = col("speed_kmh")
            if kmh is None:
                raise ValueError(f"{path}: need speed_mps or speed_kmh")
            v = kmh / 3.6

        p = col("battery_power_kw")
        soc = col("soc_percent")
        if p is None and soc is None:
            raise ValueError(
                f"{path}: need battery_power_kw or soc_percent -- without a "
                f"measured battery quantity there is nothing to validate against")

        grade = col("grade_percent")
        amb = col("ambient_c")
        doors = col("doors_open")
        return cls(
            time_s=t, speed_mps=v,
            battery_power_w=p * 1e3 if p is not None else None,
            soc=soc / 100.0 if soc is not None else None,
            grade_rad=np.arctan(grade / 100.0) if grade is not None else None,
            passengers=col("passengers"),
            doors_open=doors.astype(bool) if doors is not None else None,
            ambient_c=float(np.mean(amb)) if amb is not None else None,
            name=path.stem, source=f"csv:{path.name}",
        )

    def to_cycle(self) -> DriveCycle:
        return DriveCycle(self.time_s, self.speed_mps, self.grade_rad,
                          self.passengers, self.doors_open,
                          name=self.name, source=self.source)


@dataclass
class ValidationReport:
    log: MeasuredLog
    result: SimulationResult
    metrics: dict
    tolerance_energy: float
    tolerance_soc_rmse: float
    notes: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        m = self.metrics
        ok = True
        if m.get("energy_error") is not None:
            ok &= abs(m["energy_error"]) <= self.tolerance_energy
        if m.get("soc_rmse") is not None:
            ok &= m["soc_rmse"] <= self.tolerance_soc_rmse
        return bool(ok)

    def summary(self) -> str:
        m = self.metrics
        lines = [f"Validation: {self.result.vehicle.name} against {self.log.name}",
                 f"  distance                  {m['distance_km']:8.2f} km"]
        if m.get("energy_error") is not None:
            lines += [
                f"  measured battery energy   {m['measured_kwh']:8.2f} kWh",
                f"  simulated battery energy  {m['simulated_kwh']:8.2f} kWh",
                f"  energy error              {100*m['energy_error']:+8.2f} %   "
                f"(tolerance +/-{100*self.tolerance_energy:.1f} %)",
                f"  power correlation         {m['power_correlation']:8.3f}",
                f"  power RMSE                {m['power_rmse_kw']:8.2f} kW",
                "  energy error by phase (simulated - measured):",
            ]
            for phase, (sim, meas) in m["by_phase"].items():
                lines.append(f"    {phase:12s} {sim - meas:+8.3f} kWh "
                             f"(sim {sim:7.3f}, meas {meas:7.3f})")
        if m.get("soc_rmse") is not None:
            lines.append(f"  SOC trajectory RMSE       {100*m['soc_rmse']:8.2f} pp  "
                         f"(tolerance {100*self.tolerance_soc_rmse:.1f} pp)")
            lines.append(f"  final SOC error           {100*m['soc_final_error']:+8.2f} pp")
        lines.append(f"  RESULT                    {'PASS' if self.passed else 'FAIL'}")
        for n in self.notes:
            lines.append(f"  ! {n}")
        return "\n".join(lines)

    def write_overlay_csv(self, path) -> pathlib.Path:
        """Time-aligned measured and simulated series, for plotting anywhere."""
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        s = self.result.series
        t = s["time_s"]
        meas_p = (np.interp(t, self.log.time_s, self.log.battery_power_w)
                  if self.log.battery_power_w is not None else None)
        meas_soc = (np.interp(t, self.log.time_s, self.log.soc)
                    if self.log.soc is not None else None)
        with path.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time_s", "speed_kmh", "sim_battery_kw", "meas_battery_kw",
                        "sim_soc_percent", "meas_soc_percent"])
            for i in range(t.size):
                w.writerow([
                    f"{t[i]:.1f}", f"{s['speed_mps'][i]*3.6:.2f}",
                    f"{s['p_battery_w'][i]/1e3:.3f}",
                    f"{meas_p[i]/1e3:.3f}" if meas_p is not None else "",
                    f"{s['soc'][i]*100:.3f}",
                    f"{meas_soc[i]*100:.3f}" if meas_soc is not None else "",
                ])
        return path


def validate(vehicle: Vehicle, log: MeasuredLog, *,
             environment: Environment | None = None,
             tolerance_energy: float = 0.05,
             tolerance_soc_rmse: float = 0.015,
             dt_s: float = 1.0) -> ValidationReport:
    """Replay a measured log through the model and score the agreement."""
    env = environment or Environment(
        ambient_c=log.ambient_c if log.ambient_c is not None else 20.0,
        label="as logged")
    initial_soc = float(log.soc[0]) if log.soc is not None else None
    result = simulate(vehicle, log.to_cycle(), env, dt_s=dt_s,
                      initial_soc=initial_soc)

    s = result.series
    t = s["time_s"]
    v = s["speed_mps"]
    a = s["accel_mps2"]
    metrics: dict = {"distance_km": result.totals["distance_km"]}
    notes: list = []

    if log.battery_power_w is not None:
        meas = np.interp(t, log.time_s, log.battery_power_w)
        sim = s["p_battery_w"]
        e_meas = float(np.sum(meas) * dt_s / KWH)
        e_sim = float(np.sum(sim) * dt_s / KWH)
        metrics["measured_kwh"] = e_meas
        metrics["simulated_kwh"] = e_sim
        metrics["energy_error"] = (e_sim - e_meas) / max(abs(e_meas), 1e-9)
        metrics["power_rmse_kw"] = float(np.sqrt(np.mean((sim - meas) ** 2))) / 1e3
        metrics["power_correlation"] = (float(np.corrcoef(sim, meas)[0, 1])
                                        if np.std(meas) > 0 and np.std(sim) > 0 else 0.0)

        phases = {
            "driving": (v > 0.3) & (a >= -0.05),
            "braking": (v > 0.3) & (a < -0.05),
            "standstill": v <= 0.3,
        }
        metrics["by_phase"] = {
            k: (float(np.sum(sim[m]) * dt_s / KWH), float(np.sum(meas[m]) * dt_s / KWH))
            for k, m in phases.items()
        }

        # Diagnose on mean *power* error per phase, not energy error. A constant
        # unmodelled load spreads across every phase in proportion to time, so
        # the phase the bus spends longest in always shows the biggest kWh
        # error and would be wrongly blamed. Mean power error removes that:
        # a constant load shows up as the same offset in every phase.
        durations = {k: float(np.sum(m) * dt_s) for k, m in phases.items()}
        mean_err_kw = {
            k: (sim_ - meas_) * KWH / max(durations[k], 1.0) / 1e3
            for k, (sim_, meas_) in metrics["by_phase"].items() if durations[k] > 30.0
        }
        metrics["mean_power_error_kw"] = mean_err_kw
        total_err_kw = abs(e_sim - e_meas) * KWH / max(result.totals["duration_s"], 1.0) / 1e3
        if mean_err_kw and total_err_kw > 0.5:
            vals = list(mean_err_kw.values())
            same_sign = all(x > 0 for x in vals) or all(x < 0 for x in vals)
            mags = [abs(x) for x in vals]
            uniform = same_sign and max(mags) < 2.0 * max(min(mags), 1e-9)
            direction = "over" if (e_sim - e_meas) > 0 else "under"
            if uniform:
                yon = "fazla" if direction == "over" else "eksik"
                notes.append(Note(
                    f"model {direction}-predicts by a near-constant "
                    f"{np.mean(mags):.1f} kW in every phase, including standstill: the "
                    f"signature of a constant load -- look first at auxiliary or HVAC "
                    f"loads, not at road load or the motor",
                    f"Model her evrede, duruşta bile, yaklaşık sabit {tr_num(np.mean(mags))} kW "
                    f"{yon} tahmin ediyor. Bu sabit bir yükün izidir: önce yardımcı "
                    f"tüketicilere ve klimaya bakın, yol direncine ya da motora değil."))
            else:
                worst = max(mean_err_kw, key=lambda k: abs(mean_err_kw[k]))
                hint = {
                    "standstill": "auxiliary or HVAC loads (nothing else draws power at rest)",
                    "braking": "regen limits: blend fraction, max regen decel, "
                               "cold charge acceptance",
                    "driving": "road load (rolling resistance, Cd*A, mass) or the motor map",
                }[worst]
                hint_tr = {
                    "standstill": "yardımcı tüketiciler ve klima (duruşta başka tüketen yok)",
                    "braking": "rejenerasyon sınırları: fren paylaşımı, azami rejenerasyon "
                               "yavaşlaması, soğukta şarj kabulü",
                    "driving": "yol direnci (yuvarlanma, Cd·A, kütle) ya da motor haritası",
                }[worst]
                phase_tr = {"standstill": "duruşta", "braking": "frenlemede",
                            "driving": "sürüşte"}[worst]
                d = "over" if mean_err_kw[worst] > 0 else "under"
                notes.append(Note(
                    f"disagreement concentrates in {worst} ({d}-predicted by "
                    f"{abs(mean_err_kw[worst]):.1f} kW on average): look first at {hint}",
                    f"Fark {phase_tr} toplanıyor (ortalama {tr_num(abs(mean_err_kw[worst]))} kW "
                    f"{'fazla' if d == 'over' else 'eksik'} tahmin). Önce şuna bakın: {hint_tr}."))

    if log.soc is not None:
        meas_soc = np.interp(t, log.time_s, log.soc)
        metrics["soc_rmse"] = float(np.sqrt(np.mean((s["soc"] - meas_soc) ** 2)))
        metrics["soc_final_error"] = float(s["soc"][-1] - meas_soc[-1])

    if log.grade_rad is None:
        notes.append(Note("log has no gradient: unless the route is flat, driving-phase "
                          "error will be dominated by missing grade, not by the vehicle",
                          "Logda eğim yok: güzergâh düz değilse sürüşteki farkın çoğu "
                          "araçtan değil, eksik eğim bilgisinden gelir."))
    if log.passengers is None:
        notes.append(Note("log has no occupancy: model assumed an empty bus",
                          "Logda yolcu bilgisi yok: model otobüsü boş kabul etti."))
    if not result.achievable:
        notes.append(Note("model could not follow the logged trace -- a component "
                          "limit is set too low, since the real bus evidently managed it",
                          "Model logdaki hız profilini takip edemedi. Gerçek otobüs "
                          "başardığına göre bir bileşen sınırı gereğinden düşük girilmiş."))

    return ValidationReport(log, result, metrics, tolerance_energy,
                            tolerance_soc_rmse, notes)
