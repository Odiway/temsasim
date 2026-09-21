"""Command line interface.

    python -m temsasim list
    python -m temsasim describe citybus_12m_be
    python -m temsasim run --vehicle citybus_12m_be --cycle cbdbus --repeat 6 \
        --ambient -10 --passengers 40 --report out/winter.html
    python -m temsasim sweep --vehicle citybus_12m_be --cycle cbdbus --repeat 6
    python -m temsasim compare --vehicles citybus_12m_be,citybus_12m_be_resistive \
        --cycle cbdbus --repeat 6 --ambient -5
"""

from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np

from .cycle import Environment, Route
from .library import Library, LibraryError
from .report import write_report
from .simulator import simulate
from .validate import MeasuredLog, validate


def _occupancy(args):
    if args.passengers is not None:
        return float(args.passengers)
    if args.occupancy_profile:
        # Parabolic: empty at the termini, fullest mid-route. The shape a real
        # radial line actually has.
        peak = float(args.occupancy_profile)
        return lambda f: peak * 4.0 * f * (1.0 - f)
    return None


def _load_cycle(lib: Library, args, vehicle=None):
    occ = _occupancy(args)
    if args.route:
        route = Route.from_csv(lib.root / "route" / f"{args.route}.csv"
                               if (lib.root / "route" / f"{args.route}.csv").exists()
                               else args.route)
        print(route.summary())
        # The driver model is power-aware when it knows the vehicle, so it
        # accelerates up hills the way a real driver would.
        # Laps are driven (not copied) so traffic-light timing keeps running.
        return route.to_cycle(vehicle=vehicle, occupancy=occ, traffic=args.traffic,
                              laps=args.repeat)
    cyc = lib.load_cycle(args.cycle)
    if occ is not None:
        cyc = cyc.with_occupancy(occ)
    if args.repeat > 1:
        cyc = cyc.repeat(args.repeat)
    return cyc


def _env(args) -> Environment:
    return Environment(
        ambient_c=args.ambient,
        solar_irradiance_w_per_m2=args.solar,
        headwind_mps=args.headwind,
        night=args.night,
        label=args.label or f"{args.ambient:+.0f} degC",
    )


def cmd_list(args):
    lib = Library(args.library)
    cat = lib.catalogue()
    for kind, items in cat.items():
        if items:
            print(f"{kind:10s} {', '.join(items)}")
    print(f"{'cycle':10s} {', '.join(lib.list_cycles())}")


def cmd_describe(args):
    lib = Library(args.library)
    print(lib.load_vehicle(args.vehicle).describe())


def cmd_run(args):
    lib = Library(args.library)
    veh = lib.load_vehicle(args.vehicle)
    cyc = _load_cycle(lib, args, vehicle=veh)
    print(cyc.summary())
    print()
    result = simulate(veh, cyc, _env(args), dt_s=args.dt, soh=args.soh)
    print(result.summary())
    print()
    print("Energy balance (kWh):")
    for k, v in result.energy_balance().items():
        print(f"  {k:32s} {v:8.3f}")
    if args.report:
        p = write_report(result, args.report)
        print(f"\nReport written to {p.resolve()}")


def cmd_sweep(args):
    """Consumption against ambient temperature: the chart that ends the argument
    about whether winter range is a real problem."""
    lib = Library(args.library)
    veh = lib.load_vehicle(args.vehicle)
    cyc = _load_cycle(lib, args, vehicle=veh)
    print(cyc.summary())
    print()
    print(f"{'ambient':>8}  {'kWh/km':>8}  {'range km':>9}  {'HVAC %':>7}  "
          f"{'regen kWh':>10}  {'lost kWh':>9}")
    print("-" * 62)
    for t in np.arange(args.t_min, args.t_max + 0.01, args.t_step):
        solar = args.solar if t >= 25 else 0.0
        env = Environment(ambient_c=float(t), solar_irradiance_w_per_m2=solar,
                          label=f"{t:+.0f}C")
        r = simulate(veh, cyc, env, dt_s=args.dt, soh=args.soh)
        print(f"{t:>+7.0f}C  {r.energy_per_km_kwh:>8.3f}  {r.range_km:>9.1f}  "
              f"{100*r.totals['hvac_share']:>6.1f}%  "
              f"{r.totals['regen_recovered_kwh']:>10.2f}  "
              f"{r.totals['regen_lost_kwh']:>9.2f}")


def cmd_compare(args):
    lib = Library(args.library)
    refs = [r.strip() for r in args.vehicles.split(",")]
    # Every variant must face the identical trace, so the driver model is
    # parameterised once, from the first vehicle listed.
    cyc = _load_cycle(lib, args, vehicle=lib.load_vehicle(refs[0]))
    print(cyc.summary())
    print()
    env = _env(args)
    print(f"{'vehicle':<34}  {'kWh/km':>8}  {'range km':>9}  {'HVAC kWh':>9}")
    print("-" * 66)
    for ref in refs:
        veh = lib.load_vehicle(ref)
        r = simulate(veh, cyc, env, dt_s=args.dt, soh=args.soh)
        label = f"{veh.name} {veh.variant}".strip()[:34]
        print(f"{label:<34}  {r.energy_per_km_kwh:>8.3f}  {r.range_km:>9.1f}  "
              f"{r.totals['hvac_kwh']:>9.2f}")


def cmd_validate(args):
    """Replay a measured vehicle log through the model and score the fit."""
    lib = Library(args.library)
    veh = lib.load_vehicle(args.vehicle)
    log = MeasuredLog.from_csv(args.log)
    env = None
    if args.ambient is not None:
        env = Environment(ambient_c=args.ambient, label="override")
    rep = validate(veh, log, environment=env,
                   tolerance_energy=args.tolerance / 100.0)
    print(rep.summary())
    if args.overlay:
        print()
        print(f"overlay written to {rep.write_overlay_csv(args.overlay).resolve()}")
    return 0 if rep.passed else 1


def cmd_maraton(args):
    """Maraton GEN3 fuel and brake-pad regressions on a duty cycle."""
    from .maraton import FuelInputs, fuel_l100, pad_wear, speed_stats
    lib = Library(args.library)
    if args.route:
        route = Route.from_csv(lib.root / "route" / f"{args.route}.csv")
        cyc = route.to_cycle(traffic=args.traffic)
    else:
        cyc = lib.load_cycle(args.cycle)
    st = speed_stats(cyc.speed_mps, cyc.time_s)
    x = FuelInputs(idle_l100=args.idle, ac_pct=args.ac, v_mean_kmh=st["v_mean_kmh"],
                   rpm1200_pct=args.rpm1200, pedal_pct=args.pedal, week=args.week,
                   gt102_pct=st["gt102_pct"], gt80_pct=st["gt80_pct"], ambient_c=args.ambient,
                   kale_radiator=args.kale, body=args.body)
    f, p = fuel_l100(x), pad_wear(st["v030_pct"])
    print(f"{cyc.name}: v_mean {st['v_mean_kmh']:.1f} km/h, >80 {st['gt80_pct']:.0f} %, "
          f">102 {st['gt102_pct']:.1f} %, 0-30 km/h {st['v030_pct']:.1f} %")
    print(f"\nFuel  {f['fuel_l100']:.2f} L/100 km  (LOOCV RMSE {f['rmse_l100']} L/100 km)")
    for k, v in f["terms"].items():
        print(f"  {k:12s} {v:+7.2f}")
    print("\nBrake pads (to 20 %):")
    for w in ("front_right", "front_left", "rear"):
        print(f"  {w:12s} {p['rate_pct_per_10k'][w]:5.2f} %/10k km  {p['life_km_to_20pct'][w]:9,.0f} km")
    for wmsg in f["warnings"] + p["warnings"]:
        print("  ! " + wmsg)


def cmd_gui(args):
    from .app.server import serve
    serve(args.library, port=args.port, open_browser=not args.no_browser)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="temsasim", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--library", default="library", help="component library root")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, need_vehicle=True):
        if need_vehicle:
            sp.add_argument("--vehicle", required=True)
        sp.add_argument("--cycle", default="cbdbus")
        sp.add_argument("--route", default=None, help="route CSV instead of a cycle")
        sp.add_argument("--repeat", type=int, default=1)
        sp.add_argument("--passengers", type=float, default=None,
                        help="constant occupancy")
        sp.add_argument("--occupancy-profile", default=None,
                        help="peak occupancy of a parabolic along-route profile")
        sp.add_argument("--ambient", type=float, default=20.0)
        sp.add_argument("--solar", type=float, default=0.0, help="W/m2")
        sp.add_argument("--headwind", type=float, default=0.0, help="m/s")
        sp.add_argument("--night", action="store_true")
        sp.add_argument("--label", default=None)
        sp.add_argument("--traffic", default="yok",
                        choices=["yok", "akici", "orta", "yogun", "sikisik"],
                        help="traffic on a --route (none / light / moderate / heavy / gridlock)")
        sp.add_argument("--dt", type=float, default=1.0)
        sp.add_argument("--soh", type=float, default=1.0)

    sp = sub.add_parser("list", help="show the component library")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("describe", help="print an assembled vehicle")
    sp.add_argument("vehicle")
    sp.set_defaults(func=cmd_describe)

    sp = sub.add_parser("run", help="simulate one duty cycle")
    common(sp)
    sp.add_argument("--report", default=None, help="write an HTML report here")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("sweep", help="sweep ambient temperature")
    common(sp)
    sp.add_argument("--t-min", type=float, default=-15.0)
    sp.add_argument("--t-max", type=float, default=40.0)
    sp.add_argument("--t-step", type=float, default=5.0)
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser("compare", help="compare vehicle variants on one duty")
    common(sp, need_vehicle=False)
    sp.add_argument("--vehicles", required=True, help="comma-separated references")
    sp.set_defaults(func=cmd_compare)

    sp = sub.add_parser("validate", help="score the model against a measured log")
    sp.add_argument("--vehicle", required=True)
    sp.add_argument("--log", required=True, help="measured CAN/telematics CSV")
    sp.add_argument("--ambient", type=float, default=None,
                    help="override the logged ambient temperature")
    sp.add_argument("--tolerance", type=float, default=5.0,
                    help="energy error tolerance in percent")
    sp.add_argument("--overlay", default=None, help="write aligned sim/meas CSV")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("maraton", help="Maraton GEN3 fuel and brake-pad regressions")
    sp.add_argument("--cycle", default="hhddt65")
    sp.add_argument("--route", default=None)
    sp.add_argument("--traffic", default="yok")
    sp.add_argument("--ambient", type=float, default=20.0)
    sp.add_argument("--week", type=float, default=30.0)
    sp.add_argument("--ac", type=float, default=0.0, help="A/C usage %%")
    sp.add_argument("--rpm1200", type=float, default=17.0, help="share of time >= 1200 rpm %%")
    sp.add_argument("--pedal", type=float, default=76.0, help="mean accelerator pedal %%")
    sp.add_argument("--idle", type=float, default=1.0, help="idle fuel L/100 km")
    sp.add_argument("--kale", action="store_true", help="Kale radiator (else Raal)")
    sp.add_argument("--body", default="CD", choices=["CD", "ND"])
    sp.set_defaults(func=cmd_maraton)

    sp = sub.add_parser("gui", help="open the web interface in a browser")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--no-browser", action="store_true")
    sp.set_defaults(func=cmd_gui)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rc = args.func(args)
    except LibraryError as exc:
        print(f"library error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"not found: {exc}", file=sys.stderr)
        return 2
    return rc or 0


if __name__ == "__main__":
    raise SystemExit(main())
