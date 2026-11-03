"""Semi-empirical battery ageing.

Answers the question a fleet customer actually asks: not "what is the range
today" but "will this bus still close my route in year seven, and what will you
guarantee in writing".

Total capacity fade is treated as two independent contributions that add:

**Calendar fade** happens whether or not the bus moves, driven by storage
temperature and storage state of charge. The usual form is

    Q_cal = k_cal(T, SOC) * t**z_cal,     z_cal ~ 0.5

with an Arrhenius temperature term. The square-root-of-time shape comes from
SEI layer growth being diffusion-limited. The SOC dependence is why a depot
that leaves buses on charge at 100 % overnight is quietly destroying its own
packs, and why a smart depot finishes charging shortly before departure.

**Cycle fade** is driven by charge throughput, with C-rate and temperature
modifiers. Implemented in the form of Wang et al., *Journal of Power Sources*
196 (2011) 3942-3948, for graphite/LFP:

    Q_cyc = B(C) * exp(-(Ea - alpha*C) / (R*T)) * Ah**z_cyc,   z_cyc ~ 0.55

The defaults below are that paper's published coefficients. **They describe the
cell Wang tested, not yours.** Treat every number here as a placeholder until
you fit it to your own supplier's cycle-life data. Ageing coefficients are the
single most over-trusted numbers in this kind of model: the functional form
transfers between cells, the coefficients emphatically do not.

**A caveat that matters for a cold-climate bus.** Both terms are Arrhenius, so
this model says cold operation is unambiguously good for pack life. That is
only half true. Below roughly 10 degC, charging a graphite cell risks lithium
plating, which is an *additional* and largely irreversible fade mechanism that
this functional form does not contain at all. A bus that regenerates hard into
a cold pack every morning ages faster than these equations predict, not slower.
Until a plating term is added, treat any cold-climate life projection from this
module as optimistic, and lean on the BMS charge-acceptance derating in
:mod:`temsasim.components.battery` to keep the simulated bus from doing what
the real one is protected against.

Throughput is accumulated during simulation and extrapolated to years of
service by :func:`project_life`, which repeats a representative daily duty
rather than assuming an average.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..units import R_GAS, c_to_k


@dataclass
class AgingModel:
    name: str = "generic_nmc_aging"
    chemistry: str = "NMC"

    # --- calendar ---------------------------------------------------------
    cal_prefactor: float = 0.0010
    """Scales calendar fade. Units chosen so the result is a capacity fraction
    when time is in days. The default is set so a cell stored at the reference
    25 degC and 50 % SOC loses about 1.9 % in the first year and 6 % over ten,
    which is the middle of the published range for automotive NMC."""

    cal_activation_j_per_mol: float = 55_000.0
    cal_reference_temp_c: float = 25.0
    cal_time_exponent: float = 0.5
    cal_soc_reference: float = 0.5
    cal_soc_exponent: float = 1.4
    """Fade rises faster than linearly with storage SOC. Exponent 1.4 means
    storing at 90 % ages roughly twice as fast as storing at 50 %."""

    # --- cycling ----------------------------------------------------------
    cyc_prefactor: float = 0.0175
    """B in the Wang form, rescaled so it reads as a capacity percentage at the
    reference temperature. The default is anchored to a cell reaching 20 %
    fade at roughly 4000 equivalent full cycles at 25 degC, which is typical
    for a good automotive NMC cell. Wang's own published B values are specific
    to the LFP cell in that paper and to his absolute-Arrhenius formulation,
    so they are not interchangeable with this one."""

    cyc_activation_j_per_mol: float = 31_700.0
    cyc_c_rate_alpha: float = 370.3
    """Reduces the effective activation energy as C-rate rises, i.e. hard use
    ages the cell faster at any temperature. Wang's fitted value."""

    cyc_throughput_exponent: float = 0.55
    cyc_reference_temp_c: float = 25.0

    end_of_life_capacity: float = 0.80
    """Capacity fraction at which the pack is considered life-expired. 80 % is
    the common warranty threshold, but for a bus the meaningful criterion is
    route-based: the pack is finished when it can no longer close the worst
    scheduled block on the worst day of the year. :func:`project_life` reports
    both."""

    source: str = "Wang et al. 2011 (LFP) form; coefficients NOT TEMSA-calibrated"
    validated: bool = False
    notes: str = ""

    # ----------------------------------------------------------------------
    def calendar_fade(self, days: float, temp_c: float, mean_soc: float) -> float:
        """Capacity lost to calendar ageing, as a fraction of nominal."""
        if days <= 0.0:
            return 0.0
        arr = math.exp(-self.cal_activation_j_per_mol / R_GAS
                       * (1.0 / c_to_k(temp_c)
                          - 1.0 / c_to_k(self.cal_reference_temp_c)))
        soc_term = (max(mean_soc, 0.01) / self.cal_soc_reference) ** self.cal_soc_exponent
        return self.cal_prefactor * arr * soc_term * days ** self.cal_time_exponent

    def cycle_fade(self, throughput_ah_per_cell: float, temp_c: float,
                   mean_c_rate: float) -> float:
        """Capacity lost to charge throughput, as a fraction of nominal."""
        if throughput_ah_per_cell <= 0.0:
            return 0.0
        ea_eff = self.cyc_activation_j_per_mol - self.cyc_c_rate_alpha * mean_c_rate
        arr = math.exp(-ea_eff / (R_GAS * c_to_k(temp_c)))
        arr_ref = math.exp(-ea_eff / (R_GAS * c_to_k(self.cyc_reference_temp_c)))
        scale = arr / arr_ref
        return (self.cyc_prefactor / 100.0 * scale
                * throughput_ah_per_cell ** self.cyc_throughput_exponent)

    def soh(self, days: float, throughput_ah_per_cell: float, *,
            storage_temp_c: float, operating_temp_c: float,
            mean_soc: float, mean_c_rate: float) -> float:
        """State of health as a capacity fraction between 0 and 1."""
        fade = (self.calendar_fade(days, storage_temp_c, mean_soc)
                + self.cycle_fade(throughput_ah_per_cell, operating_temp_c, mean_c_rate))
        return float(np.clip(1.0 - fade, 0.0, 1.0))

    def describe(self) -> str:
        flag = "CALIBRATED" if self.validated else "LITERATURE COEFFICIENTS"
        return f"{self.name}: {self.chemistry}, EOL at {self.end_of_life_capacity:.0%} [{flag}]"


def project_life(
    aging: AgingModel,
    *,
    daily_throughput_ah_per_cell: float,
    mean_c_rate: float,
    mean_soc: float,
    operating_temp_c: float,
    storage_temp_c: float,
    operating_days_per_year: float = 340.0,
    years: float = 12.0,
    samples_per_year: int = 12,
):
    """Project SOH forward from one representative day of measured duty.

    Returns arrays of ``(years, soh)`` plus the year at which SOH crosses the
    end-of-life threshold, or ``None`` if it does not within the horizon.

    The extrapolation assumes the representative day repeats. That is a
    reasonable engineering approximation and a poor description of reality:
    summer and winter duty differ, routes get reassigned, and the depot's
    charging habits change. Run it per season and take the envelope before
    putting a number in a warranty document.
    """
    t_years = np.linspace(0.0, years, int(years * samples_per_year) + 1)
    soh = np.empty_like(t_years)
    eol_year = None
    for i, yr in enumerate(t_years):
        days = yr * 365.25
        op_days = yr * operating_days_per_year
        throughput = daily_throughput_ah_per_cell * op_days
        soh[i] = aging.soh(
            days, throughput,
            storage_temp_c=storage_temp_c,
            operating_temp_c=operating_temp_c,
            mean_soc=mean_soc,
            mean_c_rate=mean_c_rate,
        )
        if eol_year is None and soh[i] < aging.end_of_life_capacity:
            eol_year = float(yr)
    return t_years, soh, eol_year
