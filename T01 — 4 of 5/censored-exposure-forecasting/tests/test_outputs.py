"""Verification test harness for the population-PK exposure forecasting task.

Instruction requirement map (material requirement -> corresponding test):

  R1  /app/submission/predict.py exists and supports documented CLI .......... test_submission_predicts
  R2  OUT.csv contains exact schema, one row per subject, finite positive
      values ................................................................... test_output_format
  R3  Terminates within 120 s runtime limit .................................... test_runtime_budget
  R4  Evaluated reward = clip(1 - raw/0.5, 0, 1) >= 0.50 on held-out
      cohort ................................................................... test_accuracy_pass_bar
  R5  Outputs agree across deterministic repeated runs ......................... test_deterministic
  R6  "must write only OUT.csv": input directory remains unmodified, no extra
      files created, submission directory untouched ............................ test_inputs_unmodified
  R7  Incomplete covariates (missing row or unusable weight, age, sex) fall
      back per field to 70 kg, 45 years, and M without side-effects ............ test_covariates_*
  R8  --horizon-h H recomputes exposure over [t14, t14+H), validated against
      heldout.EXTRA_HORIZONS (1.05 / 6.05 / 24.05 / 47.95 h), default 24.0
      matches explicit 24, invalid arguments reject cleanly .................... test_horizon_*
  R9  --estimator {map,population} toggles individualization; default map
      fits random effects, population derives predictions purely from dosing
      and covariates (invariant to sample counts/concentrations/censor flags) ... test_estimator_*

Held-out ground truth is synthesized dynamically from a secret seed within a
root-only test harness. Predictions run under unprivileged agent uid against
isolated fixture directories. The test suite evaluates submission adherence and
exits with a status code consumed by the verifier pipeline runner.
"""

import csv
import hashlib
import math
import os
import shlex
import shutil
import stat
import statistics
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".truth"))
import heldout  # noqa: E402  (held-out generator under tests/.truth/, root-only)

# --- Constants exactly as stated in instruction.md ----------------------------------------------
NORMALIZE = 0.5          # reward = clip(1 - raw / 0.5, 0, 1)
W_AUC, W_CMAX, W_CTROUGH = 0.5, 0.3, 0.2
TIME_LIMIT_S = 120       # "must finish within 120 seconds" (the stated contract)
#: Wall-clock gate the suite actually asserts. This is the stated limit plus a CI/host-load
#: grace margin: on a busy host a legitimate submission a few seconds over 120 s must not
#: flake to reward 0. The grace is one-directional - it is LOOSER than the stated bar, never
#: stricter (a hidden gate tighter than the contract would be banned) - and the measured
#: value is still reported against the stated TIME_LIMIT_S. The per-run subprocess kill
#: timeout is kept strictly above this budget so a run inside the grace is measured, not killed.
BUDGET_S = TIME_LIMIT_S + 60
PASS_BAR = 0.50          # "scores 1 when this reward is at least 0.50, and 0 otherwise"
HORIZON_PASS_BAR = PASS_BAR
HZ_MAX = 168.0           # "0 < H <= 168.0"

AS_AGENT = shlex.split(os.environ.get("AS_AGENT", ""))
AGENT_UID = int(os.environ["AGENT_UID"])
AGENT_SWEEP = os.environ["AGENT_SWEEP"]

SUBMISSION = "/app/submission"
PREDICT = os.path.join(SUBMISSION, "predict.py")
OUT_COLS = ["subject_id", "auc_ss", "cmax_ss", "ctrough_ss"]
INPUT_NAMES = ("subjects.csv", "doses.csv", "samples.csv", "covariates.csv")

#: Horizons graded against held-out truth (see heldout.EXTRA_HORIZONS for why each).
HZ_GRADED = tuple(float(h) for h in heldout.EXTRA_HORIZONS)


# --------------------------------------------------------------------------------------------
# Process plumbing
# --------------------------------------------------------------------------------------------
def stop_agent():
    subprocess.run(["bash", "-c", AGENT_SWEEP], check=True)


def run_agent(cmd, *, cwd, capture_output=False, **kwargs):
    """Run ``cmd`` as the agent uid; return ``(proc, elapsed)``.

    ``elapsed`` spans ``subprocess.run`` ONLY. The agent-user sweep (verifier cleanup) runs
    after it is measured, so harness cleanup is never charged against the task's budget.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        if capture_output:
            kwargs.update(stdout=out, stderr=err)
        t0 = time.monotonic()
        try:
            proc = subprocess.run([*AS_AGENT, *cmd], cwd=cwd, **kwargs)
        finally:
            elapsed = time.monotonic() - t0
            stop_agent()
        if capture_output:
            for name, handle in (("stdout", out), ("stderr", err)):
                handle.seek(0)
                setattr(proc, name,
                        handle.read().decode(kwargs.get("encoding") or "utf-8", "replace"))
    return proc, elapsed


# --------------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------------
_ROOT_DIR = None
_INPUT_DIR = None
_TRUTH = {}
_IDS = []
_INPUT_SHA = {}
_INPUT_LISTING = []
_INPUT_OUT_EXPECTED = set()
_TOY_DIRS = []
_TOY_SHA = {}
_TOY_EXPECTED = {}
_SUB_SNAPSHOT = {}


def _sha256(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _read_truth(path):
    with open(path, newline="") as fh:
        return {r["subject_id"]: {k: float(r[k]) for k in ("auc_ss", "cmax_ss", "ctrough_ss")}
                for r in csv.DictReader(fh)}


def _tree_digest(root):
    """{relpath: fingerprint} for the regular files/symlinks under ``root``.

    Interpreter bytecode caches are skipped: they are a Python side effect, not the
    program writing a result, and the instruction exempts them.
    """
    snap = {}
    if not os.path.isdir(root):
        return snap
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):
                snap[rel] = ("link", os.readlink(full))
            elif stat.S_ISREG(st.st_mode):
                snap[rel] = ("file", st.st_size, _sha256(full))
            else:
                snap[rel] = ("other",)
    return snap


def setup_module(module):
    global _ROOT_DIR, _INPUT_DIR, _TRUTH, _IDS, _INPUT_SHA, _INPUT_LISTING, _SUB_SNAPSHOT
    _ROOT_DIR = tempfile.mkdtemp(prefix="pk-truth-")          # root-owned, mode 0700
    os.chmod(_ROOT_DIR, 0o700)
    built = os.path.join(_ROOT_DIR, "built")
    _IDS = heldout.build(built)
    _TRUTH = _read_truth(os.path.join(built, heldout.TRUTH_FILE))
    # The input dir is ROOT-owned (0755) so the agent can read the label-stripped inputs and
    # traverse it, but cannot rename/remove the directory or the `out` entry inside it (both
    # need write permission on this root-owned parent). Only the pre-created `out/`
    # subdirectory is handed to the agent, once, at setup time - never re-chowned later,
    # because os.chown follows a symlink a running predictor could plant.
    _INPUT_DIR = tempfile.mkdtemp(prefix="pk-input-")
    for name in INPUT_NAMES:
        dst = os.path.join(_INPUT_DIR, name)
        shutil.copyfile(os.path.join(built, name), dst)
        os.chmod(dst, 0o644)
    out_dir = os.path.join(_INPUT_DIR, "out")
    os.makedirs(out_dir, exist_ok=True)
    os.chmod(_INPUT_DIR, 0o755)
    os.chmod(out_dir, 0o755)
    os.chown(out_dir, AGENT_UID, -1)          # the agent owns only this leaf directory
    _INPUT_SHA = {name: _sha256(os.path.join(_INPUT_DIR, name)) for name in INPUT_NAMES}
    _INPUT_LISTING = sorted(os.listdir(_INPUT_DIR))
    _SUB_SNAPSHOT = _tree_digest(SUBMISSION)


def teardown_module(module):
    for d in (_ROOT_DIR, _INPUT_DIR, *_TOY_DIRS):
        if d:
            shutil.rmtree(d, ignore_errors=True)


def _run_predictor(outfile, timeout=BUDGET_S + 60, extra_args=None):
    """Run the submission on the held-out inputs; return (proc, elapsed, snapshot_path)."""
    # ``timeout`` is the subprocess KILL deadline, kept strictly above BUDGET_S so a run that
    # finishes inside the grace is measured (and its elapsed reported) instead of being killed.
    out_dir = os.path.join(_INPUT_DIR, "out")
    out_path = os.path.join(out_dir, outfile)
    # Record the names this suite itself asked for, so the write-only check can tell the
    # grader's own outputs apart from anything the program invented.
    _INPUT_OUT_EXPECTED.add(outfile)
    # out/ is agent-owned; never re-chown or otherwise act on it as root through a path the
    # agent controls (os.chown follows a symlink a predictor could plant).
    if os.path.islink(out_dir):
        raise AssertionError("the output directory was replaced by a symlink")
    if os.path.lexists(out_path):
        os.unlink(out_path)
    cmd = ["python3", PREDICT, "--input-dir", _INPUT_DIR, "--output", out_path]
    if extra_args:
        cmd.extend(extra_args)
    proc, elapsed = run_agent(cmd, cwd="/app", capture_output=True, text=True, timeout=timeout)
    # The output lives under an agent-writable directory, so read it the way a hostile writer
    # would have to be defended against: require the path to still resolve inside the input
    # tree, refuse to follow a symlink (a predictor could point out.csv at the enumerable
    # held-out truth), and copy the bytes into the root-only dir before any assertion sees them.
    base = os.path.realpath(_INPUT_DIR)
    target = os.path.realpath(out_path)
    if os.path.commonpath([base, target]) != base:
        raise AssertionError(f"{outfile} resolves outside the input directory: {target}")
    try:
        fd = os.open(out_path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return proc, elapsed, out_path
    except OSError as exc:
        raise AssertionError(f"{outfile} is not a readable regular file: {exc}")
    with os.fdopen(fd, "rb") as src:
        if not stat.S_ISREG(os.fstat(src.fileno()).st_mode):
            raise AssertionError(f"{outfile} must be a regular file, not a symlink or device")
        data = src.read()
    snap = os.path.join(_ROOT_DIR, "snap-" + outfile)
    with open(snap, "wb") as dst:
        dst.write(data)
    return proc, elapsed, snap


class _Cache:
    once = None
    second = None


def _predict_once():
    if _Cache.once is None:
        _Cache.once = _run_predictor("out.csv")
    return _Cache.once


def _run_second():
    """The determinism rerun, executed at most once and shared by R5 and R6."""
    if _Cache.second is None:
        _Cache.second = _run_predictor("out2.csv")
    return _Cache.second


def _read_output(path):
    assert os.path.isfile(path), "predictor wrote no output file"
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        rows = list(reader)
    return header, rows


def _raw_and_reward(rows, truth):
    errs = {"auc_ss": [], "cmax_ss": [], "ctrough_ss": []}
    for r in rows:
        sid = r["subject_id"]
        if sid not in truth:
            continue
        for col in errs:
            errs[col].append(abs(float(r[col]) - truth[sid][col]) / truth[sid][col])
    assert errs["auc_ss"], "no scored subjects in output"
    med = {k: statistics.median(v) for k, v in errs.items()}
    raw = W_AUC * med["auc_ss"] + W_CMAX * med["cmax_ss"] + W_CTROUGH * med["ctrough_ss"]
    return raw, max(0.0, min(1.0, 1.0 - raw / NORMALIZE)), med


# ---- R1 ------------------------------------------------------------------------------------
def test_submission_predicts():
    assert os.path.isfile(PREDICT), f"missing submission entrypoint {PREDICT}"
    proc, elapsed, out_path = _predict_once()
    assert proc.returncode == 0, (
        f"predictor exited {proc.returncode}\nSTDOUT:\n{proc.stdout[-3000:]}\n"
        f"STDERR:\n{proc.stderr[-3000:]}"
    )
    assert os.path.isfile(out_path), "predictor wrote no output file"


# ---- R3 ------------------------------------------------------------------------------------
def test_runtime_budget():
    proc, elapsed, out_path = _predict_once()
    # Logical progress first: a run that exits 0 having written a parseable output with the
    # full subject set demonstrably finished its work. Wall clock is checked second, against
    # the host-load grace BUDGET_S (not the stricter stated TIME_LIMIT_S), so a legitimate
    # but slightly-slow run on a loaded host does not flake the binary reward to 0.
    assert proc.returncode == 0, f"predictor exited {proc.returncode} before completing"
    assert os.path.isfile(out_path), "predictor wrote no output file"
    _, rows = _read_output(out_path)
    assert {r["subject_id"] for r in rows} == set(_IDS), (
        "predictor did not emit the full subject set")
    assert elapsed <= BUDGET_S, (
        f"predictor took {elapsed:.1f}s > {BUDGET_S}s budget "
        f"(stated limit {TIME_LIMIT_S}s; predictor process only)")


# ---- R2 ------------------------------------------------------------------------------------
def test_output_format():
    proc, elapsed, out_path = _predict_once()
    header, rows = _read_output(out_path)
    for col in OUT_COLS:
        assert col in header, f"output missing column {col!r}; header={header}"
    seen = [r["subject_id"] for r in rows]
    assert len(seen) == len(set(seen)), "duplicate subject_id rows in output"
    missing = sorted(set(_IDS) - set(seen))
    extra = sorted(set(seen) - set(_IDS))
    assert not missing and not extra, (
        f"output subject set mismatch: missing={missing[:5]} extra={extra[:5]}")
    for r in rows:
        for col in ("auc_ss", "cmax_ss", "ctrough_ss"):
            try:
                v = float(r[col])
            except (TypeError, ValueError):
                raise AssertionError(f"subject {r['subject_id']}: {col} not numeric ({r[col]!r})")
            assert math.isfinite(v) and v > 0, (
                f"subject {r['subject_id']}: {col} must be finite and positive, got {v!r}")


# ---- R4 ------------------------------------------------------------------------------------
def test_accuracy_pass_bar():
    proc, elapsed, out_path = _predict_once()
    _, rows = _read_output(out_path)
    raw, reward, med = _raw_and_reward(rows, _TRUTH)
    # Diagnostics only (never the reward file): surfaced in pytest's captured output.
    print(f"\nGRADE raw={raw:.4f} reward={reward:.4f} pass_bar={PASS_BAR} "
          f"n={len(rows)} median_rel_err={ {k: round(v, 4) for k, v in med.items()} }")
    assert reward >= PASS_BAR, (
        f"accuracy below pass bar: reward={reward:.3f} < {PASS_BAR} (raw={raw:.3f}; "
        f"median rel err AUC={med['auc_ss']:.3f}, Cmax={med['cmax_ss']:.3f}, "
        f"Ctrough={med['ctrough_ss']:.3f})"
    )


# ---- R5 ------------------------------------------------------------------------------------
def test_deterministic():
    proc, elapsed, out_path = _predict_once()
    _, first_rows = _read_output(out_path)
    first = {r["subject_id"]: r for r in first_rows}
    proc2, elapsed2, out2 = _run_second()
    assert proc2.returncode == 0, f"second run exited {proc2.returncode}"
    _, second_rows = _read_output(out2)
    second = {r["subject_id"]: r for r in second_rows}
    assert set(first) == set(second), "second run returned a different subject set"
    for sid in first:
        for col in ("auc_ss", "cmax_ss", "ctrough_ss"):
            a, b = float(first[sid][col]), float(second[sid][col])
            assert abs(a - b) <= 1e-6 * max(1.0, abs(a)), (
                f"non-deterministic output for {sid}.{col}: {a} vs {b}")
    _assert_inputs_untouched()


# ---- R6 ------------------------------------------------------------------------------------
def _assert_inputs_untouched():
    for name, digest in _INPUT_SHA.items():
        path = os.path.join(_INPUT_DIR, name)
        assert os.path.isfile(path), f"input file {name} was removed or renamed"
        assert _sha256(path) == digest, f"input file {name} was modified"
    now = sorted(os.listdir(_INPUT_DIR))
    unexpected = sorted(set(now) - set(_INPUT_LISTING) - {"out"})
    assert not unexpected, f"unexpected entries in the input directory: {unexpected}"
    out_dir = os.path.join(_INPUT_DIR, "out")
    assert not os.path.islink(out_dir), "the output directory was replaced by a symlink"
    # The grader writes only the output files it asked for; anything else under out/ is the
    # program writing where it was not told to.
    produced = sorted(os.listdir(out_dir))
    assert set(produced) <= _INPUT_OUT_EXPECTED, (
        f"your program may write only OUT.csv; found extra outputs: "
        f"{sorted(set(produced) - _INPUT_OUT_EXPECTED)}")
    for name in produced:
        assert not os.path.islink(os.path.join(out_dir, name)), (
            f"output {name} must be a regular file, not a symlink")
    # "must write only OUT.csv" also covers the program's own tree (bytecode caches exempt).
    now_sub = _tree_digest(SUBMISSION)
    added = sorted(set(now_sub) - set(_SUB_SNAPSHOT))
    removed = sorted(set(_SUB_SNAPSHOT) - set(now_sub))
    modified = sorted(k for k in set(now_sub) & set(_SUB_SNAPSHOT)
                      if now_sub[k] != _SUB_SNAPSHOT[k])
    assert not added and not removed and not modified, (
        "the run wrote to its own submission tree (only OUT.csv may be written): "
        f"added={added[:5]} removed={removed[:5]} modified={modified[:5]}")


def test_inputs_unmodified():
    # Both predictor passes must already have happened (cached); check the inputs survived.
    _predict_once()
    _run_second()
    _assert_inputs_untouched()


# ---- Toy fixtures: root-owned read-only inputs + a separate agent-owned output dir -----------


def _write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _seeded_cohort(seed, n, schedule, subject_prefix="T"):
    """Deterministic (subjects, doses, samples) rows from the task's own simulator."""
    rng = heldout.m.np.random.default_rng(seed)
    subjects, doses, samples = [], [], []
    for i in range(n):
        sid = f"{subject_prefix}{i + 1:03d}"
        dose_mg = float(rng.choice(heldout.m.DOSE_LEVELS))
        subj = heldout.m.draw_subject(rng, sid, dose_mg)
        events = heldout.m.make_dose_events(rng, dose_mg)
        subjects.append([sid, f"{dose_mg:g}"])
        for ev in events:
            t = ev.actual_h if ev.administered else ev.planned_h
            doses.append([sid, f"{t:.4f}", f"{ev.dose_mg:g}", ev.administered])
        rows = heldout.m.simulate_subject(rng, subj, events, schedule)
        rows.sort(reverse=True)   # unsorted, per the stated contract
        for t_s, obs, cens, _kind in rows:
            samples.append([sid, f"{t_s:.4f}", f"{obs:.5f}", cens])
    return subjects, doses, samples


def _make_toy(prefix, subjects, doses, samples, covariates_lines):
    """Write a toy cohort as ROOT-OWNED read-only inputs plus an agent-owned output dir.

    The leaf input directory is root-owned and mode 0555, so the agent can read (and
    traverse) the four CSVs but can neither modify, unlink nor replace them - a submission
    cannot sanitise the fixture it is graded on. The output directory is a separate sibling
    handed to the agent. Both the digests and the listing of the input dir are recorded and
    re-checked after every run.
    """
    parent = tempfile.mkdtemp(prefix=prefix)
    os.chmod(parent, 0o755)
    d = os.path.join(parent, "in")
    os.makedirs(d, exist_ok=True)
    out_dir = os.path.join(parent, "out")
    os.makedirs(out_dir, exist_ok=True)

    _write_csv(os.path.join(d, "subjects.csv"), ["subject_id", "dose_mg"], subjects)
    _write_csv(os.path.join(d, "doses.csv"),
               ["subject_id", "dose_time_h", "dose_mg", "administered"], doses)
    _write_csv(os.path.join(d, "samples.csv"),
               ["subject_id", "sample_time_h", "concentration_ng_ml", "censored"], samples)
    with open(os.path.join(d, "covariates.csv"), "w", newline="") as fh:
        fh.write("subject_id,body_weight_kg,age_years,sex\n")
        for line in covariates_lines:
            fh.write(line + "\n")
    for name in INPUT_NAMES:
        os.chmod(os.path.join(d, name), 0o644)
    _TOY_SHA[d] = {name: _sha256(os.path.join(d, name)) for name in INPUT_NAMES}
    os.chmod(d, 0o555)                       # root-owned leaf: no writes by the agent
    os.chmod(out_dir, 0o755)
    os.chown(out_dir, AGENT_UID, -1)
    _TOY_DIRS.append(parent)
    return d, out_dir


def _toy_inputs_intact(d):
    """Assert the toy inputs are byte-identical to what this suite wrote."""
    expected = _TOY_SHA[d]
    assert sorted(os.listdir(d)) == sorted(expected), (
        f"the toy input directory listing changed: {sorted(os.listdir(d))}")
    for name, digest in expected.items():
        path = os.path.join(d, name)
        assert os.path.isfile(path) and not os.path.islink(path), (
            f"toy input {name} was removed, renamed or replaced by a link")
        assert _sha256(path) == digest, f"toy input {name} was modified by the predictor"


def _run_toy(in_dir, out_dir, tag, extra_args=None):
    """Run the submission on a toy dir; return ({sid: (auc,cmax,ctrough)} | None, proc)."""
    _toy_inputs_intact(in_dir)
    name = "out-" + tag + ".csv"
    out_path = os.path.join(out_dir, name)
    if os.path.lexists(out_path):
        os.unlink(out_path)
    # The only name this run may create in its output dir, on top of the names earlier runs
    # into the same dir were told to produce.
    expected = _TOY_EXPECTED.setdefault(out_dir, set())
    expected.add(name)
    cmd = ["python3", PREDICT, "--input-dir", in_dir, "--output", out_path]
    if extra_args:
        cmd.extend(extra_args)
    proc, _elapsed = run_agent(cmd, cwd="/app", capture_output=True, text=True,
                               timeout=BUDGET_S + 60)
    _toy_inputs_intact(in_dir)
    produced = sorted(os.listdir(out_dir))
    assert set(produced) <= expected, (
        "the program wrote files other than the OUT.csv it was given into the output "
        f"directory: {sorted(set(produced) - expected)} (all: {produced})")
    if proc.returncode != 0 or not os.path.isfile(out_path) or os.path.islink(out_path):
        return None, proc
    with open(out_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    got = {}
    for r in rows:
        vals = []
        for col in ("auc_ss", "cmax_ss", "ctrough_ss"):
            try:
                v = float(r[col])
            except (TypeError, ValueError):
                return None, proc
            if not (math.isfinite(v) and v > 0):
                return None, proc
            vals.append(v)
        got[r["subject_id"]] = tuple(vals)
    return got, proc


def _complete_rows():
    return [
        "T001,64.0,40.0,F",
        "T002,95.0,55.0,M",
        "T003,112.0,61.0,F",
    ]


_COV_TOY = None


def _cov_toy(suffix, covariate_lines):
    """A toy dir on the shared 3-subject cohort; only ``covariate_lines`` varies."""
    global _COV_TOY
    if _COV_TOY is None:
        _COV_TOY = _seeded_cohort(12345, 3, heldout.m.heldout_schedule)
    subjects, doses, samples = _COV_TOY
    return _make_toy("pk-toyroot-", subjects, doses, samples, covariate_lines)


def _run_cov(suffix, covariate_lines):
    d, o = _cov_toy(suffix, covariate_lines)
    return _run_toy(d, o, suffix)


# ---- R7 (hardened): incomplete covariate rows degrade gracefully -----------------------------
def test_covariates_missing_row_falls_back():
    """A subject with NO covariate row still gets a complete finite prediction."""
    got, proc = _run_cov("miss", ["T001,64.0,40.0,F", "T003,112.0,61.0,F"])   # T002 omitted
    assert got is not None, f"predictor aborted on a missing covariate row\n{proc.stderr[-1500:]}"
    assert set(got) == {"T001", "T002", "T003"}, f"incomplete output: {sorted(got)}"
    # A second, differently shaped omission must also survive (no hard-coded subject id).
    got2, proc2 = _run_cov("miss2", ["T002,95.0,55.0,M"])
    assert got2 is not None, f"predictor aborted on a second missing-row case\n{proc2.stderr[-1500:]}"
    assert set(got2) == {"T001", "T002", "T003"}, f"incomplete output: {sorted(got2)}"


def test_covariates_unusable_weight_falls_back():
    """Blank and non-numeric weights fall back to 70 kg; a real weight is still honoured."""
    blank = ["T001,,40.0,F", "T002,95.0,55.0,M", "T003,112.0,61.0,F"]
    junk = ["T001,abc,40.0,F", "T002,95.0,55.0,M", "T003,112.0,61.0,F"]
    valid = ["T001,64.0,40.0,F", "T002,95.0,55.0,M", "T003,112.0,61.0,F"]
    g1, p1 = _run_cov("wblank", blank)
    g2, p2 = _run_cov("wjunk", junk)
    g3, p3 = _run_cov("wvalid", valid)
    assert g1 is not None, f"aborted on a blank weight\n{p1.stderr[-1500:]}"
    assert g2 is not None, f"aborted on a non-numeric weight\n{p2.stderr[-1500:]}"
    assert g3 is not None, f"aborted on a valid weight\n{p3.stderr[-1500:]}"
    assert set(g1) == {"T001", "T002", "T003"} and set(g2) == {"T001", "T002", "T003"}
    # Blank and junk (both unusable) must agree exactly - same fallback for T001.
    for a, b in zip(g1["T001"], g2["T001"]):
        assert abs(a - b) <= 1e-6 * max(1.0, abs(a)), (
            f"T001: blank-weight and junk-weight results differ ({a} vs {b})")
    # ...and that fallback MUST differ from honouring T001's real 64 kg weight.
    assert any(abs(a - b) > 1e-4 * max(1.0, abs(b))
               for a, b in zip(g1["T001"], g3["T001"])), (
        "unusable weight was not actually defaulted: its output equals the 64 kg run")
    # T002/T003 covariates are usable and unchanged, so their outputs must match across runs.
    for sid in ("T002", "T003"):
        for a, b in zip(g1[sid], g3[sid]):
            assert abs(a - b) <= 1e-6 * max(1.0, abs(a)), (
                f"{sid} changed across weight cases despite usable covariates ({a} vs {b})")


def test_covariates_unusable_sex_falls_back():
    """Blank, unknown and lowercase sex all fall back to M; a usable weight is honoured.

    ``M``/``F`` is literal: lowercase ``m``/``f`` are not those strings, so they are
    defaulted to M rather than being read as male/female.
    """
    real_m = ["T001,64.0,40.0,M", "T002,95.0,55.0,M", "T003,112.0,61.0,F"]
    real_f = ["T001,64.0,40.0,F", "T002,95.0,55.0,M", "T003,112.0,61.0,F"]
    variants = {
        "unknown": ["T001,64.0,40.0,?", "T002,95.0,55.0,M", "T003,112.0,61.0,F"],
        "blank": ["T001,64.0,40.0,", "T002,95.0,55.0,M", "T003,112.0,61.0,F"],
        "lower_f": ["T001,64.0,40.0,f", "T002,95.0,55.0,M", "T003,112.0,61.0,F"],
        "lower_m": ["T001,64.0,40.0,m", "T002,95.0,55.0,M", "T003,112.0,61.0,F"],
        "spaced": ["T001,64.0,40.0, F ", "T002,95.0,55.0,M", "T003,112.0,61.0,F"],
    }
    g_m, p_m = _run_cov("srealm", real_m)
    g_f, p_f = _run_cov("srealf", real_f)
    assert g_m is not None, f"aborted on a valid sex\n{p_m.stderr[-1500:]}"
    assert g_f is not None, f"aborted on a valid sex\n{p_f.stderr[-1500:]}"
    assert g_m["T001"] != g_f["T001"], (
        "M and F covariates produced the same forecast, so the sex effect is not applied")
    for label, lines in variants.items():
        got, proc = _run_cov("s_" + label, lines)
        assert got is not None, f"aborted on {label} sex\n{proc.stderr[-1500:]}"
        assert set(got) == {"T001", "T002", "T003"}, f"{label}: incomplete output"
        # The unusable sex must resolve to exactly the M fallback...
        for a, b in zip(got["T001"], g_m["T001"]):
            assert abs(a - b) <= 1e-6 * max(1.0, abs(a)), (
                f"{label}: T001 was not defaulted to M ({got['T001']} vs M {g_m['T001']})")
        # ...which is distinguishable from the F answer, so the check is not vacuous.
        assert any(abs(a - b) > 1e-4 * max(1.0, abs(b))
                   for a, b in zip(got["T001"], g_f["T001"])), (
            f"{label}: T001's default equals the F answer, so M was not applied")
        # The usable weight is still honoured, and other subjects are untouched.
        for sid in ("T002", "T003"):
            for a, b in zip(got[sid], g_m[sid]):
                assert abs(a - b) <= 1e-6 * max(1.0, abs(a)), (
                    f"{label}: {sid} changed despite usable covariates ({a} vs {b})")


def test_covariates_unusable_age_falls_back():
    """Blank, non-numeric and non-positive ages fall back to 45 years for that field only:
    each matches an explicit 45-year row, and the other subjects are untouched."""
    explicit = ["T001,64.0,45.0,F", "T002,95.0,55.0,M", "T003,112.0,61.0,F"]
    g_ref, p_ref = _run_cov("aref", explicit)
    assert g_ref is not None, f"aborted on a valid age\n{p_ref.stderr[-1500:]}"
    variants = {
        "blank": ["T001,64.0,,F", "T002,95.0,55.0,M", "T003,112.0,61.0,F"],
        "junk": ["T001,64.0,old,F", "T002,95.0,55.0,M", "T003,112.0,61.0,F"],
        "zero": ["T001,64.0,0,F", "T002,95.0,55.0,M", "T003,112.0,61.0,F"],
    }
    for label, lines in variants.items():
        got, proc = _run_cov("a_" + label, lines)
        assert got is not None, f"aborted on a {label} age\n{proc.stderr[-1500:]}"
        assert set(got) == {"T001", "T002", "T003"}, f"{label} age: incomplete output"
        for sid in ("T001", "T002", "T003"):
            assert _vals_equal(got[sid], g_ref[sid]), (
                f"{label} age: {sid} gave {got[sid]}, expected the 45-year answer {g_ref[sid]} "
                "(T001 should fall back to 45 years and keep its usable weight and sex; "
                "the others are unchanged)")


def test_covariates_fallback_matches_default_row():
    """A missing row must equal an explicit row of the defaults (70 kg, 45 years, M), while a
    subject whose covariates ARE usable keeps its own distinct prediction."""
    explicit = ["T001,70.0,45.0,M", "T002,95.0,55.0,M", "T003,112.0,61.0,F"]
    omitted = ["T002,95.0,55.0,M", "T003,112.0,61.0,F"]        # T001 falls back to defaults
    g_def, p1 = _run_cov("defexp", explicit)
    g_fb, p2 = _run_cov("defom", omitted)
    assert g_def is not None and g_fb is not None, "predictor failed on a default-row case"
    for a, b in zip(g_def["T001"], g_fb["T001"]):
        assert abs(a - b) <= 1e-6 * max(1.0, abs(a)), (
            f"T001 fallback {g_fb['T001']} != explicit-defaults {g_def['T001']}")
    assert g_def["T002"] != g_def["T003"], "distinct usable covariates collapsed to one value"


def test_covariates_fallback_leaves_others_unchanged():
    """Degrading one subject's covariates must not change any other subject's prediction."""
    full = _complete_rows()
    broken_mid = ["T001,64.0,40.0,F", "T003,112.0,61.0,F"]     # T002 defaulted
    g_full, p1 = _run_cov("leavefull", full)
    g_brk, p2 = _run_cov("leavebrk", broken_mid)
    assert g_full is not None and g_brk is not None, "predictor failed on a leave-others case"
    for sid in ("T001", "T003"):                                # untouched subjects
        for a, b in zip(g_full[sid], g_brk[sid]):
            assert abs(a - b) <= 1e-6 * max(1.0, abs(a)), (
                f"{sid} changed when only T002's covariates were defaulted ({a} vs {b})")


# ---- R8 (hardened rung 2): caller-controlled forecast horizon --------------------------------
def _heldout_horizon(horizon):
    """Root-only path of the held-out truth CSV for a forecast window of ``horizon`` hours."""
    path = os.path.join(_ROOT_DIR, "built", heldout.truth_name(horizon))
    assert os.path.isfile(path), f"missing held-out truth for horizon {horizon}"
    return path


def test_horizon_default_matches_explicit_24():
    """Omitting --horizon-h is identical to passing --horizon-h 24."""
    d, o = _cov_toy("hzdef", _complete_rows())
    g_default, p0 = _run_toy(d, o, "hzdef_a")
    g_24, p1 = _run_toy(d, o, "hzdef_b", ["--horizon-h", "24"])
    assert g_default is not None, f"default run failed\n{p0.stderr[-1500:]}"
    assert g_24 is not None, f"explicit 24h run failed\n{p1.stderr[-1500:]}"
    assert set(g_default) == set(g_24) == {"T001", "T002", "T003"}
    for sid in g_default:
        for a, b in zip(g_default[sid], g_24[sid]):
            assert abs(a - b) <= 1e-6 * max(1.0, abs(a)), (
                f"{sid}: default horizon differs from explicit 24h ({a} vs {b})")


def _check_horizon_accuracy(horizon, extra_args):
    truth = _read_truth(_heldout_horizon(horizon))
    tag = ("%g_%s" % (horizon, "_".join(extra_args))).replace(".", "p").replace(" ", "")
    proc, elapsed, out_path = _run_predictor(
        "outh_%s.csv" % tag, extra_args=["--horizon-h", repr(horizon), *extra_args])
    assert proc.returncode == 0, (
        f"horizon {horizon}h {extra_args} run exited {proc.returncode}\n{proc.stderr[-1500:]}")
    assert os.path.isfile(out_path), f"horizon {horizon}h {extra_args} wrote no output file"
    assert elapsed <= BUDGET_S, (
        f"horizon {horizon}h {extra_args} run took {elapsed:.1f}s > {BUDGET_S}s budget "
        f"(stated limit {TIME_LIMIT_S}s)")
    _, rows = _read_output(out_path)
    got_ids = {r["subject_id"] for r in rows}
    assert got_ids == set(_IDS), (
        f"horizon {horizon}h {extra_args}: subject set mismatch "
        f"({len(got_ids)} of {len(_IDS)} ids)")
    raw, reward, med = _raw_and_reward(rows, truth)
    print(f"\nHORIZON {horizon}h {extra_args} raw={raw:.4f} reward={reward:.4f} "
          f"median_rel_err={ {k: round(v, 4) for k, v in med.items()} }")
    assert reward >= HORIZON_PASS_BAR, (
        f"horizon {horizon}h {extra_args}: reward={reward:.3f} < {HORIZON_PASS_BAR} "
        f"(raw={raw:.3f}; median rel err AUC={med['auc_ss']:.3f}, "
        f"Cmax={med['cmax_ss']:.3f}, Ctrough={med['ctrough_ss']:.3f}) - the targets are "
        f"the quantities of exactly [t14, t14+{horizon}) with the trough at exactly t14+H")


def test_horizon_accuracy_matches_truth():
    """For each graded non-default horizon the submitted values must match the held-out
    truth of EXACTLY [t14, t14+H), at the same reward bar as the default window.

    The windows are not multiples of a plausible integration step, so a predictor that
    rounds the window onto a grid node, drops the partial final interval, or scales the
    24 h answer by H/24 misses the bar by a wide margin.
    """
    for horizon in HZ_GRADED:
        _check_horizon_accuracy(horizon, [])


#: Horizon pairs (H, H+delta) for the window-additivity identity. ``delta`` is far below
#: any plausible integration step, so a predictor that rounds the window onto its grid
#: reports an AUC difference of 0 (both endpoints collapse) or of a whole grid step.
_HZ_ADDITIVITY = ((6.07, 0.03), (24.13, 0.04))


def test_horizon_window_additivity():
    """The window boundary must actually move with H: widening the window by a tiny delta
    must add approximately ``delta * Ctrough(H+delta)`` to the AUC, since the added sliver
    of curve is delta wide with the trough's concentration.

    A predictor whose window snaps to a fixed grid reports AUC differences of either 0 or a
    whole grid step for a sub-step delta, so this ratio leaves [0.75, 1.3] by a wide margin
    even though its 24 h answer is accurate. Each toy cohort fit is the reference's own; the
    check only binds the three outputs to each other through calculus, not to hidden truth.
    """
    d, o = _cov_toy("hzadd", _complete_rows())
    for H, delta in _HZ_ADDITIVITY:
        a, pa = _run_toy(d, o, "hzadd_a_%g" % H, ["--horizon-h", repr(H)])
        b, pb = _run_toy(d, o, "hzadd_b_%g" % H, ["--horizon-h", repr(H + delta)])
        assert a is not None, f"H={H} run failed\n{pa.stderr[-1500:]}"
        assert b is not None, f"H={H + delta} run failed\n{pb.stderr[-1500:]}"
        assert set(a) == set(b) == {"T001", "T002", "T003"}
        for sid in a:
            d_auc = b[sid][0] - a[sid][0]
            predicted = delta * b[sid][2]
            assert predicted > 0, f"H={H}: trough must be positive, got {b[sid][2]!r}"
            ratio = d_auc / predicted
            assert 0.75 <= ratio <= 1.3, (
                f"{sid} H={H}->{H + delta}: widening the window added {d_auc:.6g} to the AUC "
                f"but delta*Ctrough(H+delta) = {predicted:.6g} (ratio {ratio:.3f}) - the "
                "window close did not move by the requested delta, so --horizon-h is being "
                "rounded to an integration step rather than honoured exactly")
            # Cmax is a maximum over the window, so it can only grow (or stay put) when the
            # window widens - never shrink beyond the ~1e-4 the differing node layout can
            # cost - and the trough must differ between the two windows, since it sits at a
            # different time.
            assert b[sid][1] >= a[sid][1] * (1.0 - 1e-3), (
                f"{sid} H={H}->{H + delta}: Cmax shrank when the window only grew "
                f"({a[sid][1]} -> {b[sid][1]})")
            assert abs(b[sid][2] - a[sid][2]) > 1e-6 * max(1.0, abs(a[sid][2])), (
                f"{sid} H={H}->{H + delta}: trough did not move with the window close "
                f"({a[sid][2]} == {b[sid][2]})")


def test_optional_modes_are_deterministic():
    """The same guarantee holds for an optional-mode invocation: two runs with the same
    --horizon-h and --estimator arguments must agree."""
    d, o = _cov_toy("detopt", _complete_rows())
    args = ["--horizon-h", "6.05", "--estimator", "population"]
    a, pa = _run_toy(d, o, "detopt_a", args)
    b, pb = _run_toy(d, o, "detopt_b", args)
    assert a is not None and b is not None, (
        f"optional-mode run failed\n{(pa if a is None else pb).stderr[-1500:]}")
    assert set(a) == set(b), "the repeated optional-mode run returned a different subject set"
    for sid in a:
        assert _vals_equal(a[sid], b[sid], rel=1e-6), (
            f"{sid}: repeated optional-mode run disagreed {a[sid]} vs {b[sid]}")


def test_horizon_rejects_invalid_values():
    """Valid horizons are accepted; zero/negative/non-numeric/NaN/inf/out-of-range values
    exit non-zero and write no output."""
    d, o = _cov_toy("hzbad", _complete_rows())
    for good in ("36", "24", "0.25"):
        got, proc = _run_toy(d, o, "hzgood_" + good.replace(".", "p"), ["--horizon-h", good])
        assert got is not None, (
            f"the valid horizon {good!r} was rejected\n{proc.stderr[-1500:]}")
    for i, bad in enumerate(("0", "-5", "abc", "nan", "inf", "-inf", "", str(HZ_MAX + 1),
                             str(HZ_MAX * 2))):
        name = f"bad_h{i}.csv"
        out_path = os.path.join(o, name)
        if os.path.lexists(out_path):
            os.unlink(out_path)
        proc, _elapsed = run_agent(
            ["python3", PREDICT, "--input-dir", d, "--output", out_path, "--horizon-h", bad],
            cwd="/app", capture_output=True, text=True, timeout=BUDGET_S + 60,
        )
        assert proc.returncode != 0, f"horizon {bad!r} was accepted (exit 0)"
        assert not os.path.exists(out_path), f"horizon {bad!r} still produced an output file"
        _toy_inputs_intact(d)
    # ...and the rejected runs left nothing behind in the output directory.
    produced = sorted(os.listdir(o))
    assert all(n.startswith("out-hzgood_") for n in produced), (
        f"the program wrote unexpected files: {produced}")


# ---- R9 (hardened rung 3): caller-selected estimation strategy -------------------------------
_EST_DEFAULT = "map"


def _est_toy(suffix, sample_multiplier=1.0, transform=None):
    """A toy cohort whose samples may be rescaled or transformed; doses/covariates fixed.

    Two calls share the exact same subjects/doses/covariates and differ only in the
    measured concentrations, which is what isolates sample dependence.
    """
    rng = heldout.m.np.random.default_rng(424242)
    subjects, doses, samples = [], [], []
    for i in range(3):
        sid = f"T{i + 1:03d}"
        dose_mg = float(rng.choice(heldout.m.DOSE_LEVELS))
        subj = heldout.m.draw_subject(rng, sid, dose_mg)
        events = heldout.m.make_dose_events(rng, dose_mg)
        subjects.append([sid, f"{dose_mg:g}"])
        for ev in events:
            t = ev.actual_h if ev.administered else ev.planned_h
            doses.append([sid, f"{t:.4f}", f"{ev.dose_mg:g}", ev.administered])
        rows = list(heldout.m.simulate_subject(rng, subj, events, heldout.m.heldout_schedule))
        rows = transform(rows) if transform else rows
        for t_s, obs, cens, _kind in rows:
            conc = obs * sample_multiplier if not cens else obs
            samples.append([sid, f"{t_s:.4f}", f"{conc:.5f}", cens])
    return _make_toy("pk-estroot-", subjects, doses, samples, _complete_rows())


def _vals_differ(a, b, rel=1e-4):
    return any(abs(x - y) > rel * max(1.0, abs(y)) for x, y in zip(a, b))


def _vals_equal(a, b, rel=1e-6):
    return all(abs(x - y) <= rel * max(1.0, abs(y)) for x, y in zip(a, b))


def test_estimator_default_matches_explicit_map():
    """Omitting --estimator is identical to passing --estimator map."""
    d, o = _est_toy("estdef", 1.0)
    g_def, p0 = _run_toy(d, o, "estdef_a")
    g_map, p1 = _run_toy(d, o, "estdef_b", ["--estimator", _EST_DEFAULT])
    assert g_def is not None, f"default run failed\n{p0.stderr[-1500:]}"
    assert g_map is not None, f"explicit map run failed\n{p1.stderr[-1500:]}"
    for sid in g_def:
        assert _vals_equal(g_def[sid], g_map[sid]), (
            f"{sid}: default estimator differs from explicit map {g_def[sid]} vs {g_map[sid]}")


def test_estimator_population_ignores_samples():
    """In population mode the forecast must not depend on the samples AT ALL: not on their
    values, their timing, their censor flags, how many rows there are, or their presence."""
    base_d, base_o = _est_toy("estbase", 1.0)
    base_pop, pb = _run_toy(base_d, base_o, "popbase", ["--estimator", "population"])
    assert base_pop is not None, f"population run failed\n{pb.stderr[-1500:]}"

    def shift_time(rows):
        return [(t + 0.5, obs, cens, kind) for t, obs, cens, kind in rows]

    def censor_all(rows):
        return [(t, 0.0, 1, kind) for t, obs, cens, kind in rows]

    def drop_two(rows):
        return rows[2:]

    def add_one(rows):
        return rows + [(rows[0][0] + 0.25, rows[0][1] * 1.1, 0, rows[0][3])]

    perturbations = (("scaled", 1.7, None), ("timing", 1.0, shift_time),
                     ("censored", 1.0, censor_all), ("fewer_rows", 1.0, drop_two),
                     ("extra_row", 1.0, add_one))
    for label, mult, transform in perturbations:
        d_k, o_k = _est_toy("est_" + label, mult, transform)
        pop_k, p_k = _run_toy(d_k, o_k, "pop" + label, ["--estimator", "population"])
        assert pop_k is not None, (
            f"population run failed on the {label} fixture\n{p_k.stderr[-1500:]}")
        assert set(pop_k) == set(base_pop), f"{label}: incomplete output {sorted(pop_k)}"
        for sid in base_pop:
            assert _vals_equal(base_pop[sid], pop_k[sid]), (
                f"{label} {sid}: population forecast changed with the samples "
                f"({base_pop[sid]} vs {pop_k[sid]}) - it must depend only on dosing and "
                "covariates")

    # Invariance control: in map mode a value scaling MUST move the forecast, so the checks
    # above cannot be satisfied by a predictor that ignores the samples in every mode.
    d_s, o_s = _est_toy("estmapscaled", 1.7)
    map_base, m0 = _run_toy(base_d, base_o, "mapbase", ["--estimator", "map"])
    map_scaled, m1 = _run_toy(d_s, o_s, "mapscaled", ["--estimator", "map"])
    assert map_base is not None and map_scaled is not None, (
        f"map runs failed\n{(map_base or (None, m0))[1].stderr[-1500:]}")
    moved = [sid for sid in map_base if _vals_differ(map_base[sid], map_scaled[sid])]
    assert moved, (
        "map forecasts did not change at all when every measured concentration was scaled "
        "by 1.7 - the estimator option appears to be ignored")


def test_estimator_population_ignores_subject_id():
    """Distinct subject IDs with identical dose rows and covariates but different samples
    must receive IDENTICAL population forecasts (the forecast may not depend on the id)."""
    rng = heldout.m.np.random.default_rng(5150)
    dose_mg = float(rng.choice(heldout.m.DOSE_LEVELS))
    events = heldout.m.make_dose_events(rng, dose_mg)
    dose_rows = [[f"{(ev.actual_h if ev.administered else ev.planned_h):.4f}",
                  f"{ev.dose_mg:g}", str(ev.administered)] for ev in events]
    ids = ("T001", "T002", "T003", "T004")
    subjects, doses, samples = [], [], []
    for i, sid in enumerate(ids):
        subjects.append([sid, f"{dose_mg:g}"])
        for t, dm, adm in dose_rows:
            doses.append([sid, t, dm, adm])
        if i == 2:
            continue                      # T003: identical inputs, no samples at all
        subj = heldout.m.draw_subject(rng, sid, dose_mg)
        rows = list(heldout.m.simulate_subject(rng, subj, events, heldout.m.heldout_schedule))
        for j, (t_s, obs, cens, _k) in enumerate(rows):
            k = 1.0 + 0.35 * i * (j + 1)
            samples.append([sid, f"{t_s:.4f}", f"{obs * k:.5f}", cens])
    cov_lines = [f"{sid},72.5,44.0,M" for sid in ids]
    d, o = _make_toy("pk-sametoy-", subjects, doses, samples, cov_lines)
    pop, p = _run_toy(d, o, "popsame", ["--estimator", "population"])
    assert pop is not None, f"population run failed\n{p.stderr[-1500:]}"
    assert set(pop) == set(ids), f"incomplete output: {sorted(pop)}"
    first = pop[ids[0]]
    for sid in ids[1:]:
        assert _vals_equal(first, pop[sid]), (
            f"{sid} and {ids[0]} share identical dose rows and covariates but their "
            f"population forecasts differ ({pop[sid]} vs {first}) - population mode must "
            "ignore both the samples and the subject identity")
    # Control: with the same identical dosing/covariates the MAP forecasts DO separate, so
    # the equality above reflects the estimator ignoring samples/ids rather than a constant
    # output that would collapse every subject in every mode.
    gmap, pm = _run_toy(d, o, "same_map", ["--estimator", "map"])
    assert gmap is not None, f"map run failed\n{pm.stderr[-1500:]}"
    distinct = {gmap[sid] for sid in ids}
    assert len(distinct) >= 2, (
        "map forecasts were identical across distinct subjects with different samples - "
        "the fixture is not discriminating")


def test_estimator_population_responds_to_dosing_and_covariates():
    """Population mode must still be a forecast, not a constant: subjects that differ only
    in their dose, or only in their body weight, must get different values."""
    rng = heldout.m.np.random.default_rng(909)
    events = heldout.m.make_dose_events(rng, 8.0)
    # One canonical sample block, reused verbatim for every subject in every variant, so the
    # sample rows cannot explain any difference in the population forecasts.
    srng = heldout.m.np.random.default_rng(11)
    canon = heldout.m.simulate_subject(srng, heldout.m.draw_subject(srng, "CANON", 8.0),
                                       events, heldout.m.heldout_schedule)
    ids = ("T001", "T002", "T003")

    def cohort(dose_of, weight_of):
        subjects, doses, samples = [], [], []
        for i, sid in enumerate(ids):
            dose_mg = dose_of[i]
            subjects.append([sid, f"{dose_mg:g}"])
            for ev in events:
                t = ev.actual_h if ev.administered else ev.planned_h
                doses.append([sid, f"{t:.4f}", f"{ev.dose_mg * dose_mg / 8.0:g}",
                              ev.administered])
            samples.extend([sid, f"{t:.4f}", f"{obs:.5f}", cens]
                           for t, obs, cens, _k in canon)
        cov = [f"{sid},{weight_of[i]},45.0,M" for i, sid in enumerate(ids)]
        return subjects, doses, samples, cov

    # Same weight and covariates, three different dose levels.
    d_dose, o_dose = _make_toy("pk-poppd-", *cohort({0: 4.0, 1: 8.0, 2: 16.0},
                                                    {0: 72.5, 1: 72.5, 2: 72.5}))
    pop_dose, p1 = _run_toy(d_dose, o_dose, "popdose", ["--estimator", "population"])
    assert pop_dose is not None, f"population run failed\n{p1.stderr[-1500:]}"
    aucs = {pop_dose[sid][0] for sid in ("T001", "T002", "T003")}
    assert len(aucs) == 3, (
        f"population AUC did not separate three dose levels ({sorted(aucs)}) - a forecast "
        "must depend on the dose")
    assert pop_dose["T001"][0] < pop_dose["T002"][0] < pop_dose["T003"][0], (
        f"population AUC is not ordered by dose: {pop_dose}")

    # Same dose and covariates, three different body weights.
    d_wt, o_wt = _make_toy("pk-popwt-", *cohort({0: 8.0, 1: 8.0, 2: 8.0},
                                                {0: 55.0, 1: 75.0, 2: 105.0}))
    pop_wt, p2 = _run_toy(d_wt, o_wt, "popwt", ["--estimator", "population"])
    assert pop_wt is not None, f"population run failed\n{p2.stderr[-1500:]}"
    weights_out = {pop_wt[sid] for sid in ("T001", "T002", "T003")}
    assert len(weights_out) == 3, (
        f"population forecast did not separate three body weights ({sorted(weights_out)}) - "
        "a forecast must depend on the covariates")


def test_estimator_population_differs_from_map():
    """On the SAME inputs, population and map must give different forecasts (map adjusts to
    the subject's samples). Checked on two distinct sample scalings so a constant cannot win."""
    for mult, tag in ((1.0, "d0"), (2.0, "d2")):
        d, o = _est_toy("estdiff" + tag, mult)
        pop, pp = _run_toy(d, o, "dp" + tag, ["--estimator", "population"])
        mp, pm = _run_toy(d, o, "dm" + tag, ["--estimator", "map"])
        assert pop is not None and mp is not None, f"runs failed (scale {mult})"
        differing = [sid for sid in pop if _vals_differ(pop[sid], mp[sid])]
        assert differing, (
            f"scale {mult}: population and map gave identical forecasts for every subject "
            "- the estimator option appears to be ignored")


def test_estimator_rejects_invalid_values():
    """Exactly ``map`` and ``population`` are accepted; any other literal string - including
    case and whitespace variants - exits non-zero and writes no output."""
    d, o = _est_toy("estbad", 1.0)
    for i, good_val in enumerate(("map", "population")):
        out_path = os.path.join(o, f"good_est_{i}.csv")
        proc, _elapsed = run_agent(
            ["python3", PREDICT, "--input-dir", d, "--output", out_path,
             "--estimator", good_val],
            cwd="/app", capture_output=True, text=True, timeout=BUDGET_S + 60,
        )
        assert proc.returncode == 0 and os.path.isfile(out_path), (
            f"the valid estimator {good_val!r} was rejected\n{proc.stderr[-1500:]}")
        _toy_inputs_intact(d)
    bad_values = ("avg", "MAP2", "", "population2", "MAP", "Population", " map", "map ",
                  " population", "map\n", "poPulation", "Map", "populations")
    for i, bad in enumerate(bad_values):
        name = f"bad_est_{i}.csv"
        out_path = os.path.join(o, name)
        if os.path.lexists(out_path):
            os.unlink(out_path)
        proc, _elapsed = run_agent(
            ["python3", PREDICT, "--input-dir", d, "--output", out_path,
             "--estimator", bad],
            cwd="/app", capture_output=True, text=True, timeout=BUDGET_S + 60,
        )
        assert proc.returncode != 0, f"estimator {bad!r} was accepted (exit 0)"
        assert not os.path.exists(out_path), (
            f"estimator {bad!r} still produced an output file")
        _toy_inputs_intact(d)
    # ...and the rejected runs left nothing behind beyond the two accepted outputs.
    produced = sorted(os.listdir(o))
    assert set(produced) <= {"good_est_0.csv", "good_est_1.csv"}, (
        f"the program wrote unexpected files: {produced}")
