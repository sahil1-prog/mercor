"""Population oral one-compartment PK simulator with autoinduction.

This module is the single source of truth for the generative process behind the
multiple-ascending-dose study.  It is imported by the visible-data generator
(``authoring/generate_data.py``) and by the held-out verifier generator.  It is
never copied into the agent image.

Model (per subject i, weight WT_i in kg):

    ka_i   = KA    * exp(eta_ka)
    V_i    = V     * (WT_i/70)^1.00 * exp(eta_V) * SEX_V[sex_i]
    CL0_i  = CL0   * (WT_i/70)^0.75 * exp(eta_CL) * SEX_CL[sex_i]
    Emax_i = EMAX  * exp(eta_Emax)                      (fold autoinduction)
    ET50_i = ET50  * exp(eta_ET50)                      (hours)

    CL_i(t) = CL0_i * (1 + Emax_i * t^h / (ET50_i^h + t^h)),  t = hours since first dose
              (t clamped at >= 0 so CL is defined for any time)

Absorption from a gut depot with first-order rate ka into a central compartment
of apparent volume V, eliminated with apparent clearance CL_i(t):

    dA_gut/dt = -ka A_gut + (dose inputs)
    dA_cent/dt = ka A_gut - (CL_i(t)/V) A_cent
    C(t) = A_cent / V * 1000              (ng/mL, dose in mg, V in L)

The time-varying CL makes the ODE linear time-varying; we integrate on a fine
uniform grid treating CL as piecewise constant on each step with the exact
two-compartment analytic update (Gut exponential + exact Ac response), which is
accurate to ~1e-4 relative at dt = 0.02 h.

Assay: C_obs = C_true*(1+eps_prop) + eps_add, eps_prop ~ N(0, SIG_PROP),
eps_add ~ N(0, SIG_ADD).  A sample with C_obs < LLOQ is censored (BLQ).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# Population typical values (the numbers a modeller would try to recover)
# ---------------------------------------------------------------------------
KA_POP = 1.1          # 1/h  absorption rate
V_POP = 300.0         # L    apparent volume of distribution
CL0_POP = 15.0        # L/h  baseline apparent clearance (at treatment start)
EMAX_POP = 1.4        # fold autoinduction of clearance at t -> inf
ET50_POP = 120.0      # h    time to half-maximal induction
HILL = 1.6            # dimensionless  steepness of induction curve

SEX_V = {"M": 1.06, "F": 0.95}    # volume scaling by sex
SEX_CL = {"M": 1.05, "F": 0.95}   # clearance scaling by sex

# Inter-individual variability (log-normal SDs on the log scale)
SD_KA = 0.20
SD_V = 0.14
SD_CL = 0.16
SD_EMAX = 0.18
SD_ET50 = 0.14

# Residual (assay) error
SIG_PROP = 0.10       # proportional
SIG_ADD = 0.20        # ng/mL additive
LLOQ = 1.00           # ng/mL

# Dosing regimen
N_DAYS = 14
DOSE_INTERVAL = 24.0  # h
DOSE_LEVELS = (4.0, 8.0, 16.0)  # mg, ascending-dose study
N_DOSES = N_DAYS      # one dose each morning

# Late / missed dose behaviour
P_LATE = 0.15         # each dose independently late with this prob
LATE_MAX = 4.0        # ... by U(0.5, LATE_MAX) hours
P_MISSED = 0.06       # each dose independently missed with this prob

# Integration
DT_GRID = 0.02        # h, truth integration step


def cl_at(t, cl0, emax, et50, hill=HILL):
    """Apparent clearance at hours-since-first-dose ``t`` (array-safe, t>=0)."""
    t = np.maximum(t, 0.0)
    tn = np.power(t, hill)
    return cl0 * (1.0 + emax * tn / (np.power(et50, hill) + tn))


@dataclass
class Subject:
    subject_id: str
    dose_mg: float
    weight_kg: float
    age_years: float
    sex: str
    ka: float
    V: float
    cl0: float
    emax: float
    et50: float
    # populated by sim
    dose_rows: list = field(default_factory=list)  # (t_h, dose_mg, administered)


@dataclass
class DoseEvent:
    planned_h: float
    actual_h: float
    dose_mg: float
    administered: int


def draw_subject(rng, subject_id, dose_mg):
    wt = float(np.clip(rng.normal(70.0, 12.0), 45.0, 120.0))
    age = float(np.clip(rng.normal(45.0, 12.0), 18.0, 75.0))
    sex = "M" if rng.random() < 0.5 else "F"
    ka = KA_POP * math.exp(rng.normal(0.0, SD_KA))
    V = V_POP * (wt / 70.0) ** 1.0 * SEX_V[sex] * math.exp(rng.normal(0.0, SD_V))
    cl0 = CL0_POP * (wt / 70.0) ** 0.75 * SEX_CL[sex] * math.exp(rng.normal(0.0, SD_CL))
    emax = max(0.05, EMAX_POP * math.exp(rng.normal(0.0, SD_EMAX)))
    et50 = ET50_POP * math.exp(rng.normal(0.0, SD_ET50))
    return Subject(subject_id, dose_mg, wt, age, sex, ka, V, cl0, emax, et50)


def make_dose_events(rng, dose_mg):
    """Build the 14 planned doses with realistic late / missed behaviour."""
    events = []
    for k in range(N_DOSES):
        planned = k * DOSE_INTERVAL
        administered = 1
        if rng.random() < P_MISSED:
            administered = 0
        late = 0.0
        if administered and rng.random() < P_LATE:
            late = float(rng.uniform(0.5, LATE_MAX))
        events.append(
            DoseEvent(planned_h=planned, actual_h=planned + late,
                      dose_mg=dose_mg, administered=administered)
        )
    return events


def day14_time(events):
    """t14 = largest dose_time_h over all rows (administered or not)."""
    return max(ev.actual_h for ev in events)


def _simulate_concentration(subj, events, t_end, dt=DT_GRID):
    """Return (grid, conc_ng_ml) over [0, t_end] with fine uniform grid."""
    n = int(round(t_end / dt)) + 1
    grid = np.arange(n, dtype=float) * dt
    conc = np.empty(n, dtype=float)
    # bucket administered doses by their grid index (actual time)
    doses_at = {}
    for ev in events:
        if ev.administered:
            j = int(round(ev.actual_h / dt))
            if 0 <= j < n:
                doses_at[j] = doses_at.get(j, 0.0) + ev.dose_mg
    agut = 0.0
    ac = 0.0
    conc[0] = ac / subj.V * 1000.0
    ka = subj.ka
    for i in range(n - 1):
        if i in doses_at:
            agut += doses_at[i]
        t_mid = grid[i] + 0.5 * dt
        cl = float(cl_at(t_mid, subj.cl0, subj.emax, subj.et50))
        k = cl / subj.V
        # exact update of the linear system over dt with constant ka, k
        e_ka = math.exp(-ka * dt)
        e_k = math.exp(-k * dt)
        agut_new = agut * e_ka
        if abs(k - ka) > 1e-9:
            ac_new = ac * e_k + ka * agut * (e_ka - e_k) / (k - ka)
        else:
            ac_new = ac * e_k + ka * agut * dt * e_ka
        agut, ac = agut_new, ac_new
        conc[i + 1] = ac / subj.V * 1000.0
    return grid, conc


def _node_grid(t_end, marks=(), dt=DT_GRID):
    """Nodes from 0 to ``t_end`` (both exact) with every mark also an exact node.

    Sub-steps are equal within each marked segment and no wider than ``dt``, so the
    window ``[t14, t14+H]`` never loses a partial step at either end.
    """
    cuts = sorted({0.0, float(t_end)} | {float(m) for m in marks if 0.0 <= float(m) <= float(t_end)})
    parts = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        span = b - a
        n = max(1, int(math.ceil(span / dt - 1e-9)))
        part = a + np.arange(n + 1, dtype=float) * (span / n)
        parts.append(part[:-1])
    parts.append(np.array([float(t_end)]))
    return np.concatenate(parts)


def _simulate_on(subj, events, grid):
    """Concentration at the supplied nodes (exact two-compartment update per step)."""
    n = grid.size
    conc = np.empty(n, dtype=float)
    doses_at = {}
    for ev in events:
        if ev.administered:
            j = int(np.searchsorted(grid, ev.actual_h, side="left"))
            if 0 <= j < n:
                doses_at[j] = doses_at.get(j, 0.0) + ev.dose_mg
    agut = 0.0
    ac = 0.0
    conc[0] = ac / subj.V * 1000.0
    ka = subj.ka
    for i in range(n - 1):
        if i in doses_at:
            agut += doses_at[i]
        step = float(grid[i + 1] - grid[i])
        t_mid = float(grid[i]) + 0.5 * step
        cl = float(cl_at(t_mid, subj.cl0, subj.emax, subj.et50))
        k = cl / subj.V
        e_ka = math.exp(-ka * step)
        e_k = math.exp(-k * step)
        agut_new = agut * e_ka
        if abs(k - ka) > 1e-9:
            ac_new = ac * e_k + ka * agut * (e_ka - e_k) / (k - ka)
        else:
            ac_new = ac * e_k + ka * agut * step * e_ka
        agut, ac = agut_new, ac_new
        conc[i + 1] = ac / subj.V * 1000.0
    return conc


def true_exposures(subj, events, horizon=DOSE_INTERVAL):
    """True AUCss / Cmaxss / Ctroughss of the window ``[t14, t14 + horizon)``.

    ``t14`` is the largest dose time over all planned rows; the window close is exactly
    ``t14 + horizon`` and both ends are integration nodes, so the trough is the
    concentration at exactly ``t14 + horizon`` and the AUC covers the whole window
    (including a partial step when the horizon is not a multiple of the grid step).
    """
    t14 = day14_time(events)
    t_end = t14 + float(horizon)
    grid = _node_grid(t_end, marks=(t14,))
    conc = _simulate_on(subj, events, grid)
    m = (grid >= t14 - 1e-12) & (grid <= t_end + 1e-12)
    seg_t = grid[m]
    seg_c = conc[m]
    auc = float(np.trapezoid(seg_c, seg_t))
    cmax = float(np.max(seg_c))
    ctrough = float(conc[-1])
    return {"auc_ss": auc, "cmax_ss": max(cmax, 1e-9), "ctrough_ss": max(ctrough, 1e-9)}


def observe(rng, c_true):
    """Apply proportional + additive assay error; return (observed, censored)."""
    c_obs = c_true * (1.0 + rng.normal(0.0, SIG_PROP)) + rng.normal(0.0, SIG_ADD)
    if c_obs < LLOQ:
        return 0.0, 1  # placeholder value; not a measurement
    return float(c_obs), 0


def rich_schedule(events):
    """Dense day-1 and day-14 profiles plus intermediate morning troughs."""
    t14 = day14_time(events)
    times = []
    day1_off = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 20.0, 24.0]
    times += [(ev_t, "day1") for ev_t in day1_off]
    # intermediate pre-dose troughs (mornings of days 2..13)
    for k in range(1, N_DAYS - 1):
        times.append((k * DOSE_INTERVAL, "trough"))
    day14_off = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 20.0, 24.0]
    times += [(t14 + off, "day14") for off in day14_off]
    return times


def sparse_schedule(events):
    """A few day-1, day-7 and day-11 samples; pre-dose troughs often fall BLQ."""
    times = []
    # day-1 profile: absorption-ish and a first-trough
    for off in [0.75, 2.5, 6.0, 12.0]:
        times.append((off, "day1"))
    # pre-dose troughs on day 7 and day 11 (the induction signal) plus one
    # post-dose day-7 sample
    times.append((events[6].actual_h, "trough"))          # day-7 pre-dose
    times.append((events[6].actual_h + 3.0, "day7"))
    times.append((events[10].actual_h, "trough"))         # day-11 pre-dose
    return times


def heldout_schedule(events):
    """Sampling design for the GRADED held-out cohort.

    This MUST match the visible sparse design: a handful of day-1 / day-7 /
    day-11 samples and NO sample inside the day-14 window ``[t14, t14+24)``.
    Using ``rich_schedule`` here would hand the day-14 profile to the predictor
    and collapse the forecasting task to trivial integration; the verifier must
    import THIS function.  ``assert_no_day14_samples`` enforces the invariant.
    """
    return sparse_schedule(events)


def assert_no_day14_samples(events, schedule_fn=heldout_schedule):
    """Guard the held-out design: no graded sample may reveal day-14 exposure."""
    t14 = day14_time(events)
    lo, hi = t14 - 1e-6, t14 + DOSE_INTERVAL + 1e-6
    leaked = [t for t, _ in schedule_fn(events) if lo <= t <= hi]
    if leaked:
        raise AssertionError(
            f"held-out schedule leaks day-14 samples in [t14,t14+24): {leaked}"
        )
    return True


def simulate_subject(rng, subj, events, schedule_fn):
    """Return list of sample rows: (sample_time_h, concentration, censored)."""
    t14 = day14_time(events)
    t_end = t14 + DOSE_INTERVAL
    grid, conc = _simulate_concentration(subj, events, t_end)
    rows = []
    for t_s, kind in schedule_fn(events):
        if t_s < 0 or t_s > t_end + 1e-9:
            continue
        c_true = float(np.interp(t_s, grid, conc))
        obs, cens = observe(rng, c_true)
        rows.append((round(t_s, 4), obs, cens, kind))
    return rows
