"""Generative population pharmacokinetic simulator with enzyme autoinduction.

Authoritative source of truth for the clinical study simulation. Calibrates both
the training datasets and unseen evaluation cohort using independent seeds.
Isolated within the verification image and excluded from candidate workspaces.
"""

Model (per subject i; WT in kg, AGE in years):

    ka_i    = KA    * exp(eta_ka)
    tlag_i  = TLAG  * exp(eta_tlag)                       absorption lag (h)
    V_i     = V     * (WT/70)^1.00 * SEX_V[sex] * exp(eta_V)
    CL0_i   = CL0   * (WT/70)^0.75 * (AGE/45)^AGE_CL_EXP * SEX_CL[sex] * exp(eta_CL)
    Emax_i  = EMAX  * exp(eta_Emax)                       max fold increase of enzyme
    EC50_i  = EC50  * exp(eta_EC50)                       ng/mL
    kenz_i  = KENZ  * exp(eta_kenz)                       1/h enzyme turnover

Enzyme turnover (indirect response, stimulation of synthesis by plasma drug):

    dE/dt = kenz * (1 + Emax * C / (EC50 + C)) - kenz * E,   E(0) = 1
    CL(t) = CL0 * E(t)

so induction depends on how much drug the subject actually had on board: higher dose
arms induce more, and missed doses let the enzyme pool decay back toward baseline.
Exposure is therefore NOT proportional to dose, and a subject who steps up to a higher
dose mid-study induces further after the step.

Absorption: each administered dose enters a gut depot at (actual time + tlag) and is
absorbed first-order with rate ka into a central compartment of apparent volume V.

    C(t) = A_cent / V * 1000              (ng/mL; dose in mg, V in L)

Integration: node grid no wider than DT_GRID, with every dose-entry time and every
requested window boundary as an exact node. On each step the enzyme is advanced with
the exact solution for the concentration held at its start-of-step value, the step's
mean enzyme level sets the elimination rate, and the gut/central pair is advanced with
the exact linear update. At DT_GRID = 0.02 h this agrees with a 0.005 h solve to
better than 1e-4 relative on every graded quantity.

Assay: C_obs = C_true*(1+eps_prop) + eps_add. C_obs < LLOQ is reported as censored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# Population typical values
# ---------------------------------------------------------------------------
KA_POP = 1.1          # 1/h
TLAG_POP = 0.40       # h
V_POP = 300.0         # L
CL0_POP = 15.0        # L/h  pre-induction apparent clearance
EMAX_POP = 12.0       # max fold enzyme increase (never reached: C stays far below EC50)
EC50_POP = 30.0       # ng/mL
KENZ_POP = math.log(2.0) / 96.0   # 1/h  (enzyme half-life 96 h)

AGE_CL_EXP = -0.35    # clearance falls with age
SEX_V = {"M": 1.06, "F": 0.95}
SEX_CL = {"M": 1.05, "F": 0.95}

# Inter-individual variability (log-normal SDs)
SD_KA = 0.30
SD_TLAG = 0.30
SD_V = 0.20
SD_CL = 0.30
SD_EMAX = 0.35
SD_EC50 = 0.40
SD_KENZ = 0.25

# Residual (assay) error
SIG_PROP = 0.12
SIG_ADD = 0.30        # ng/mL
LLOQ = 2.50           # ng/mL

# Dosing regimen
N_DAYS = 14
DOSE_INTERVAL = 24.0
DOSE_LEVELS = (4.0, 8.0, 16.0)
N_DOSES = N_DAYS

#: Within-subject titration: from day ESCALATE_DAY on, an escalating subject takes
#: ESCALATE_FACTOR times its starting dose. Not every subject escalates.
ESCALATE_DAY = 8
ESCALATE_FACTOR = 2.0
P_ESCALATE = 0.7

P_LATE = 0.15
LATE_MAX = 4.0
P_MISSED = 0.06

DT_GRID = 0.02        # h


@dataclass
class Subject:
    subject_id: str
    dose_mg: float
    weight_kg: float
    age_years: float
    sex: str
    ka: float
    tlag: float
    V: float
    cl0: float
    emax: float
    ec50: float
    kenz: float
    dose_rows: list = field(default_factory=list)


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
    tlag = TLAG_POP * math.exp(rng.normal(0.0, SD_TLAG))
    V = V_POP * (wt / 70.0) * SEX_V[sex] * math.exp(rng.normal(0.0, SD_V))
    cl0 = (CL0_POP * (wt / 70.0) ** 0.75 * (age / 45.0) ** AGE_CL_EXP * SEX_CL[sex]
           * math.exp(rng.normal(0.0, SD_CL)))
    emax = EMAX_POP * math.exp(rng.normal(0.0, SD_EMAX))
    ec50 = EC50_POP * math.exp(rng.normal(0.0, SD_EC50))
    kenz = KENZ_POP * math.exp(rng.normal(0.0, SD_KENZ))
    return Subject(subject_id, dose_mg, wt, age, sex, ka, tlag, V, cl0, emax, ec50, kenz)


def make_dose_events(rng, dose_mg):
    """The 14 planned daily doses: starting dose ``dose_mg``, doubled from day
    ESCALATE_DAY for subjects who escalate, with realistic late and missed behaviour."""
    escalate = rng.random() < P_ESCALATE
    events = []
    for k in range(N_DOSES):
        mg = dose_mg * ESCALATE_FACTOR if escalate and k + 1 >= ESCALATE_DAY else dose_mg
        planned = k * DOSE_INTERVAL
        administered = 0 if rng.random() < P_MISSED else 1
        late = 0.0
        if administered and rng.random() < P_LATE:
            late = float(rng.uniform(0.5, LATE_MAX))
        events.append(DoseEvent(planned, planned + late, mg, administered))
    return events


def day14_time(events):
    """t14 = largest dose_time_h over all rows (administered or not)."""
    return max(ev.actual_h for ev in events)


def _dose_entries(subj, events):
    """(entry time into the gut, mg) for every administered dose."""
    return [(ev.actual_h + subj.tlag, ev.dose_mg) for ev in events if ev.administered]


def _node_grid(t_end, marks=(), dt=DT_GRID):
    """Nodes from 0 to ``t_end`` (both exact), every mark in range an exact node, and
    equal sub-steps no wider than ``dt`` between consecutive marks."""
    cuts = sorted({0.0, float(t_end)} | {float(m) for m in marks if 0.0 <= float(m) <= float(t_end)})
    parts = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        n = max(1, int(math.ceil((b - a) / dt - 1e-9)))
        parts.append(a + np.arange(n, dtype=float) * ((b - a) / n))
    parts.append(np.array([float(t_end)]))
    return np.concatenate(parts)


def _simulate_on(subj, events, grid):
    """Concentration (ng/mL) at every node of ``grid``."""
    n = grid.size
    conc = np.zeros(n, dtype=float)
    doses_at = {}
    for t_in, mg in _dose_entries(subj, events):
        j = int(np.searchsorted(grid, t_in - 1e-9, side="left"))
        if j < n:
            doses_at[j] = doses_at.get(j, 0.0) + mg
    ka, V, cl0 = subj.ka, subj.V, subj.cl0
    emax, ec50, kenz = subj.emax, subj.ec50, subj.kenz
    agut = ac = 0.0
    enz = 1.0
    for i in range(n - 1):
        if i in doses_at:
            agut += doses_at[i]
        h = float(grid[i + 1] - grid[i])
        c_now = ac / V * 1000.0
        e_ss = 1.0 + emax * c_now / (ec50 + c_now)
        enz_new = e_ss + (enz - e_ss) * math.exp(-kenz * h)
        k = cl0 * 0.5 * (enz + enz_new) / V
        e_ka = math.exp(-ka * h)
        e_k = math.exp(-k * h)
        if abs(k - ka) > 1e-9:
            ac = ac * e_k + ka * agut * (e_ka - e_k) / (k - ka)
        else:
            ac = ac * e_k + ka * agut * h * e_ka
        agut *= e_ka
        enz = enz_new
        conc[i + 1] = ac / V * 1000.0
    return conc


def true_exposures_multi(subj, events, horizons):
    """{horizon: {auc_ss, cmax_ss, ctrough_ss}} for windows ``[t14, t14 + H)``.

    One integration covers every requested window: t14 and each window close are exact
    nodes, so the trough is the concentration at exactly ``t14 + H`` and each AUC spans
    the whole window, partial final step included.
    """
    t14 = day14_time(events)
    ends = [t14 + float(h) for h in horizons]
    marks = [t14, *ends, *(t for t, _ in _dose_entries(subj, events))]
    grid = _node_grid(max(ends), marks=marks)
    conc = _simulate_on(subj, events, grid)
    out = {}
    lo = int(np.searchsorted(grid, t14 - 1e-9))
    for h, t_end in zip(horizons, ends):
        hi = int(np.searchsorted(grid, t_end - 1e-9))
        seg_t, seg_c = grid[lo:hi + 1], conc[lo:hi + 1]
        out[h] = {
            "auc_ss": float(np.trapezoid(seg_c, seg_t)),
            "cmax_ss": max(float(np.max(seg_c)), 1e-9),
            "ctrough_ss": max(float(conc[hi]), 1e-9),
        }
    return out


def true_exposures(subj, events, horizon=DOSE_INTERVAL):
    return true_exposures_multi(subj, events, (float(horizon),))[float(horizon)]


def observe(rng, c_true):
    """Apply proportional + additive assay error; return (observed, censored)."""
    c_obs = c_true * (1.0 + rng.normal(0.0, SIG_PROP)) + rng.normal(0.0, SIG_ADD)
    if c_obs < LLOQ:
        return 0.0, 1
    return float(c_obs), 0


def rich_schedule(events):
    """Dense day-1 and day-14 profiles plus morning pre-dose troughs on days 2..13."""
    t14 = day14_time(events)
    times = [(off, "day1") for off in
             [0.25, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 20.0, 24.0]]
    for k in range(1, N_DAYS - 1):
        times.append((events[k].actual_h, "trough"))
    times += [(t14 + off, "day14") for off in
              [0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 20.0, 24.0]]
    return times


def sparse_schedule(events):
    """A few day-1 samples, a day-4 pre-dose trough, and a day-7 pre-dose trough plus
    post-dose sample - all on the starting dose, before any titration step. Low-dose
    troughs often fall below the LLOQ."""
    times = [(off, "day1") for off in [0.75, 2.5, 6.0, 12.0]]
    times.append((events[3].actual_h, "trough"))
    times.append((events[6].actual_h, "trough"))
    times.append((events[6].actual_h + 3.0, "day7"))
    return times


def heldout_schedule(events):
    """Sampling design for the GRADED cohort: identical to the visible sparse design, and
    never a sample inside the day-14 window (``assert_no_day14_samples`` enforces it)."""
    return sparse_schedule(events)


def assert_no_day14_samples(events, schedule_fn=heldout_schedule):
    t14 = day14_time(events)
    lo, hi = t14 - 1e-6, t14 + DOSE_INTERVAL + 1e-6
    leaked = [t for t, _ in schedule_fn(events) if lo <= t <= hi]
    if leaked:
        raise AssertionError(f"held-out schedule leaks day-14 samples: {leaked}")
    return True


def simulate_subject(rng, subj, events, schedule_fn):
    """Sample rows (sample_time_h, observed, censored, kind) for one subject."""
    sched = schedule_fn(events)
    t_end = max(day14_time(events) + DOSE_INTERVAL, max(t for t, _ in sched))
    marks = [t for t, _ in sched] + [t for t, _ in _dose_entries(subj, events)]
    grid = _node_grid(t_end, marks=marks)
    conc = _simulate_on(subj, events, grid)
    rows = []
    for t_s, kind in sched:
        if t_s < 0 or t_s > t_end + 1e-9:
            continue
        c_true = float(np.interp(t_s, grid, conc))
        obs, cens = observe(rng, c_true)
        rows.append((round(t_s, 4), obs, cens, kind))
    return rows
