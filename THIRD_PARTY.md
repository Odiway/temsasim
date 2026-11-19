# Third-party material

temsasim's own code and data are TEMSA's. Two parts of this repository are derived from
other works and carry those works' terms.

## VECTO (European Commission) — EUPL-1.2

VECTO, the Vehicle Energy Consumption calculation TOol, is developed for the European
Commission (DG CLIMA, JRC) and published at https://code.europa.eu/vecto/vecto under the
European Union Public Licence v. 1.2 (https://joinup.ec.europa.eu/collection/eupl/eupl-text-eupl-12).
(c) European Union.

Derived from VECTO, and therefore distributed under the EUPL-1.2 as well:

| File | Derived from | What was changed |
|---|---|---|
| `temsasim/shift.py` | `VectoCore/Models/SimulationComponent/Impl/Shiftstrategies/AMTShiftStrategyOptimized.cs`, `BaseShiftStrategy.cs`, `Models/Declaration/DeclarationData.cs` (`Gearbox`, `GearboxTCU`, `ComputeEfficiencyShiftPolygon`), `Models/SimulationComponent/Data/Gearbox/ShiftPolygon.cs`, `Data/Engine/EngineFullLoadCurve.cs` | Translated from C# to Python and restructured for a backward-facing solver that follows a prescribed speed trace: VECTO's dry runs became a pre-computed table, the driver state is read off the trace, the traction interruption enters the decision only. Three extensions were added and are marked as such (braking downshift, skipped gears on a polygon downshift, start-gear cap), and a kick-down rule for traces that demand more than a gear can deliver. Every constant is a set point. Translated November 2026 from the "stable" branch (changelog head v5.1.3). |
| `library/route/vecto_coach.csv`, `vecto_interurban.csv`, `vecto_suburban.csv`, `vecto_urban.csv`, `vecto_heavy_urban.csv` | `VectoCore/Resources/Declaration/MissionCycles/Coach.vdri`, `Interurban.vdri`, `Suburban.vdri`, `Urban.vdri`, `HeavyUrban.vdri` | Resampled to a 10 m grid, the gradient integrated to an elevation profile, stop times attached to stop positions (`temsasim/vecto_cycles.py`). |

Values taken from VECTO's declaration tables and used as defaults elsewhere (fuel
properties, wheel inertia and dynamic radius, alternator efficiency, passenger mass, the
quarter-of-the-glazing solar rule as a reference for `solar_exposed_share`) are facts and
are cited where they are used.

The VECTO source tree itself is not part of this repository.

## ADVISOR (NREL)

The standard drive cycles under `library/cycle` come from ADVISOR, (c) Alliance for
Sustainable Energy, LLC, under a permissive licence (`advisor/license.txt`). temsasim
contains no ADVISOR code.
