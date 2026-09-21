"""The Maraton regressions reproduce the report's own numbers."""

import numpy as np
import pytest

from temsasim.maraton import FuelInputs, fuel_l100, pad_wear, speed_stats


@pytest.mark.parametrize("v030,life_km", [(11.1, 359_000), (11.6, 338_000), (14.2, 260_000), (35.4, 64_000)])
def test_pad_life_matches_report_table(v030, life_km):
    """Report slide 16, 'Model estimate' column (front-right wheel to 20 %)."""
    assert pad_wear(v030)["life_km_to_20pct"]["front_right"] == pytest.approx(life_km, rel=0.015)


def test_pad_rear_wear_rate_matches_report():
    """Slide 14: v0-30 8 % -> 12 % takes the rear axle from 1.20 to 2.14 % per 10k km."""
    assert pad_wear(8)["rate_pct_per_10k"]["rear"] == pytest.approx(1.20, abs=0.02)
    assert pad_wear(12)["rate_pct_per_10k"]["rear"] == pytest.approx(2.14, abs=0.02)


def test_pad_flags_urban_extrapolation():
    assert pad_wear(35)["warnings"] and not pad_wear(11)["warnings"]


def _delta(**change):
    base = FuelInputs()
    return fuel_l100(FuelInputs(**{**base.__dict__, **change}))["fuel_l100"] - fuel_l100(base)["fuel_l100"]


def test_fuel_unit_effects_match_report_tornado():
    """Report slides 3-4. Confirms the units: >102 share in points, >80 share
    as a fraction in the temperature term."""
    assert _delta(gt102_pct=5) == pytest.approx(0.18, abs=0.01)
    assert _delta(pedal_pct=81) == pytest.approx(0.29, abs=0.01)
    assert _delta(rpm1200_pct=22) == pytest.approx(0.60, abs=0.01)
    assert _delta(kale_radiator=True, ambient_c=30) - _delta(ambient_c=30) == pytest.approx(0.30, abs=0.01)
    assert _delta(ac_pct=48, v_mean_kmh=78) - _delta(v_mean_kmh=78) == pytest.approx(3.19, abs=0.05)
    t = FuelInputs(gt80_pct=60)
    assert (fuel_l100(FuelInputs(**{**t.__dict__, "ambient_c": 27}))["fuel_l100"]
            - fuel_l100(FuelInputs(**{**t.__dict__, "ambient_c": 11}))["fuel_l100"]) == pytest.approx(-0.71, abs=0.05)


def test_season_peaks_in_february():
    weeks = np.arange(1, 53)
    season = [fuel_l100(FuelInputs(week=w))["terms"]["season"] for w in weeks]
    assert 4 <= weeks[int(np.argmax(season))] <= 9
    assert max(season) == pytest.approx(0.97, abs=0.02)      # amplitude (report: 0.94 ± CI)


def test_nd_body_is_minus_six_and_a_half_percent():
    cd = fuel_l100(FuelInputs())["fuel_l100"]
    nd = fuel_l100(FuelInputs(body="ND"))["fuel_l100"]
    assert nd / cd == pytest.approx(0.935)


def test_speed_stats_from_a_trace():
    v = np.r_[np.zeros(100), np.full(300, 20 / 3.6), np.full(600, 90 / 3.6)]
    s = speed_stats(v)
    assert s["v030_pct"] == pytest.approx(40.0, abs=0.2)
    assert s["gt80_pct"] == pytest.approx(60.0, abs=0.2)
    assert s["v_mean_kmh"] == pytest.approx((300 * 20 + 600 * 90) / 900, rel=1e-6)
