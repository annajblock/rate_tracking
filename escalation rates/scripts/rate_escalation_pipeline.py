"""
CA Utility Rate Escalation Pipeline
------------------------------------
Consumes the lineage-tagged export from the DBeaver SQL query (JSON or CSV)
and computes:

  1. A single blended $/kWh energy rate and blended $/kW demand rate per
     tariff version, using the weekday/weekend TOU schedules as hour-weights.
     This makes versions comparable even when the number or definition of
     TOU periods changed between them (e.g. a TOU redesign).
  2. Period-over-period escalation (CAGR) between each tariff version and
     its predecessor, via the family_id / version_seq lineage from SQL.
  3. Trailing 5-year and 10-year CAGR for each tariff family's current
     (latest) version.

ASSUMPTION TO REVISIT: within a TOU period, tiered rates are collapsed to a
single number via TIER_INDEX (default 0 = first/lowest tier). If you have a
representative usage profile instead of "always use the first tier", swap
out pick_tier_rate() for something that picks a tier based on assumed usage.

Repo layout this script expects (paths below are resolved relative to this
file's own location, not your current working directory, so it works no
matter where you run it from):
    inputs/   <- source JSON/CSV exports go here
    outputs/  <- generated CSVs land here
    scripts/  <- this file

Usage:
    python rate_escalation_pipeline.py                              # uses defaults below
    python rate_escalation_pipeline.py path/to/export.json
    python rate_escalation_pipeline.py path/to/export.json path/to/out_dir
"""

import json
import math
from pathlib import Path

import pandas as pd

TIER_INDEX = 0  # <-- change this if you want a different tier convention

# Escalation values with |CAGR| above this get flagged for manual review
# rather than dropped — usually a sign the "prior" blended rate was near
# zero (a data artifact) rather than a real rate change of that size.
CAGR_REVIEW_THRESHOLD = 0.50  # 50%/year

DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]  # non-leap reference year
WEEKDAY_FRACTION = 5 / 7
WEEKEND_FRACTION = 2 / 7

JSON_COLUMNS = [
    "energyratestructure", "energyweekdayschedule", "energyweekendschedule",
    "demandratestructure", "demandweekdayschedule", "demandweekendschedule",
    "flatdemandstructure", "flatdemandmonths",
]

# Resolved relative to this file, not the caller's cwd -- so this script
# works the same whether you run it from the repo root, from inside
# scripts/, or from anywhere else.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_INPUT = REPO_ROOT / "inputs" / "sql_output_w_descriptions.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs"


# ---------- Loading ----------

def safe_json_load(val):
    if val is None:
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if isinstance(val, (list, dict)):
        return val
    val = str(val).strip()
    if val == "" or val.lower() == "null":
        return None
    return json.loads(val)


def load_export(path: str) -> pd.DataFrame:
    if str(path).endswith(".json"):
        with open(path, "r") as f:
            raw = json.load(f)
        # DBeaver's JSON resultset exporter wraps the actual rows in a dict
        # keyed by the SQL query text itself, e.g. {"<the sql query>": [ {...}, ... ]}
        # instead of a plain list of records. Unwrap that if we see it.
        if isinstance(raw, dict) and len(raw) == 1:
            raw = next(iter(raw.values()))
        df = pd.DataFrame(raw)
    else:
        df = pd.read_csv(path)
    for col in JSON_COLUMNS:
        if col in df.columns:
            df[col] = df[col].apply(safe_json_load)
    for date_col in ["startdate", "enddate", "prior_startdate"]:
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], utc=True, errors="coerce")
    return df


# ---------- Hour-weighting from TOU schedules ----------

def period_hour_weights(weekday_schedule, weekend_schedule):
    """Returns {period_index: hours_per_year} using a generic 5/7-weekday,
    2/7-weekend split, so results don't depend on which literal calendar
    year the tariff happened to be filed in."""
    weights = {}
    if not weekday_schedule or not weekend_schedule:
        return weights
    for month in range(12):
        wd_hours = DAYS_IN_MONTH[month] * WEEKDAY_FRACTION
        we_hours = DAYS_IN_MONTH[month] * WEEKEND_FRACTION
        for hour in range(24):
            p_wd = int(weekday_schedule[month][hour])
            p_we = int(weekend_schedule[month][hour])
            weights[p_wd] = weights.get(p_wd, 0) + wd_hours
            weights[p_we] = weights.get(p_we, 0) + we_hours
    return weights


def pick_tier_rate(period_tiers, key="rate"):
    if not period_tiers:
        return None
    idx = min(TIER_INDEX, len(period_tiers) - 1)
    val = period_tiers[idx].get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def blended_rate(rate_structure, weekday_schedule, weekend_schedule):
    """Hour-weighted blended $/unit across TOU periods for one tariff version."""
    if not rate_structure:
        return None
    weights = period_hour_weights(weekday_schedule, weekend_schedule)
    if not weights:
        # No schedule available (e.g. non-TOU rate) — just average the periods
        rates = [pick_tier_rate(p) for p in rate_structure if pick_tier_rate(p) is not None]
        return sum(rates) / len(rates) if rates else None
    weighted_sum = 0.0
    covered_hours = 0.0
    for period_idx, hours in weights.items():
        if period_idx >= len(rate_structure):
            continue
        rate = pick_tier_rate(rate_structure[period_idx])
        if rate is None:
            continue
        weighted_sum += rate * hours
        covered_hours += hours
    return weighted_sum / covered_hours if covered_hours else None


def blended_flat_demand(flatdemandstructure, flatdemandmonths):
    """Simple 12-month average of the flat (non-TOU) demand charge."""
    if not flatdemandstructure or not flatdemandmonths:
        return None
    monthly_rates = []
    for month in range(12):
        group_idx = int(flatdemandmonths[month])
        if group_idx >= len(flatdemandstructure):
            continue
        rate = pick_tier_rate(flatdemandstructure[group_idx])
        if rate is not None:
            monthly_rates.append(rate)
    return sum(monthly_rates) / len(monthly_rates) if monthly_rates else None


# ---------- Apply to every row ----------

def _is_missing(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def sum_optional(a, b):
    """Add two possibly-missing components together. If BOTH are missing,
    the tariff simply has no charge of this kind at all -> None. If only
    one is missing, treat it as 0 so the other component still counts."""
    if _is_missing(a) and _is_missing(b):
        return None
    return (0 if _is_missing(a) else a) + (0 if _is_missing(b) else b)


def add_blended_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["blended_energy_rate"] = df.apply(
        lambda r: blended_rate(r["energyratestructure"], r["energyweekdayschedule"], r["energyweekendschedule"]),
        axis=1,
    )
    df["blended_demand_rate"] = df.apply(
        lambda r: blended_rate(r["demandratestructure"], r["demandweekdayschedule"], r["demandweekendschedule"]),
        axis=1,
    )
    df["blended_flat_demand_rate"] = df.apply(
        lambda r: blended_flat_demand(r["flatdemandstructure"], r["flatdemandmonths"]),
        axis=1,
    )
    # Real bills pay both the flat/seasonal demand charge AND the TOU demand
    # charge simultaneously when both exist (see the B-19 example) — this is
    # the "what a customer actually pays per kW" figure.
    df["blended_total_demand_rate"] = df.apply(
        lambda r: sum_optional(r["blended_demand_rate"], r["blended_flat_demand_rate"]), axis=1
    )
    return df


def add_family_display_name(df: pd.DataFrame) -> pd.DataFrame:
    """label/family_id are opaque hashes — add a human-readable name you can
    actually filter/sort/group by. family_display_name uses the *latest*
    version's name in each family, so every row in a family shares one
    consistent label even if the name drifted slightly across versions."""
    df = df.copy()
    sorted_df = df.sort_values(["family_id", "version_seq"])
    latest_name = sorted_df.groupby("family_id")["name"].last()
    latest_utility = sorted_df.groupby("family_id")["utility_name"].last()
    df["family_display_name"] = df["family_id"].map(latest_name)
    df["family_utility_name"] = df["family_id"].map(latest_utility)
    return df


# ---------- Escalation math ----------

def cagr(rate_new, rate_old, years):
    if rate_new is None or rate_old is None or years is None:
        return None
    if isinstance(rate_new, float) and math.isnan(rate_new):
        return None
    if isinstance(rate_old, float) and math.isnan(rate_old):
        return None
    if rate_old <= 0 or rate_new <= 0 or years <= 0:
        # CAGR isn't meaningful for zero/negative rates (a handful of USURDB
        # rows have negative "rate" values, e.g. credits) — skip those rather
        # than let Python's ** return a complex number.
        return None
    return (rate_new / rate_old) ** (1 / years) - 1


def needs_review(cagr_value, threshold=CAGR_REVIEW_THRESHOLD):
    """Flag implausible escalation values for manual review. Doesn't drop
    anything — just marks rows worth a second look."""
    if cagr_value is None:
        return False
    if isinstance(cagr_value, float) and math.isnan(cagr_value):
        return False
    return abs(cagr_value) > threshold


def add_period_over_period_escalation(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["family_id", "version_seq"]).copy()

    # Carry the predecessor's name along so you can see, right next to each
    # escalation number, exactly which named rate it's a step from — not
    # just a label hash. Also flags when the name itself changed between
    # versions (a real rename/redesign vs. just a routine rate update).
    df["prior_name"] = df.groupby("family_id")["name"].shift(1)
    df["name_changed_from_prior"] = df["prior_name"].notna() & (df["name"] != df["prior_name"])

    # The SQL-computed yrs_since_prior comes from
    # EXTRACT(YEAR FROM age(...)) + EXTRACT(MONTH FROM age(...))/12, which
    # truncates to whole months and silently drops leftover days — that can
    # meaningfully skew CAGR on short intervals since years is an exponent.
    # Recompute exactly from the date columns instead (both are already
    # parsed datetimes at this point).
    df["yrs_since_prior_exact"] = (df["startdate"] - df["prior_startdate"]).dt.days / 365.25

    value_cols = [
        "blended_energy_rate", "blended_demand_rate", "blended_flat_demand_rate",
        "blended_total_demand_rate", "fixed_charge_first_meter", "min_charge",
    ]
    for col in value_cols:
        if col not in df.columns:
            continue
        prev_col = f"prior_{col}"
        df[prev_col] = df.groupby("family_id")[col].shift(1)
        esc_col = f"{col}_escalation_cagr"
        df[esc_col] = df.apply(
            lambda r, c=col, p=prev_col: cagr(r[c], r[p], r.get("yrs_since_prior_exact")), axis=1
        )
        df[f"{esc_col}_needs_review"] = df[esc_col].apply(needs_review)
    return df


def trailing_window_escalation(df: pd.DataFrame, years: int, value_col: str) -> pd.DataFrame:
    """For each family's latest version, find the version whose startdate is
    closest to (latest_startdate - years) and compute CAGR to that point.

    Every family gets a row in the output, even if a CAGR couldn't be
    computed — "status"/"reason" explain why, instead of the family just
    silently disappearing (e.g. a tariff genuinely only has 4 years of
    history in the source data, so there's no real "5 years ago" version
    to compare against)."""
    cagr_col = f"{value_col}_cagr_{years}yr"
    review_col = f"{cagr_col}_needs_review"
    results = []

    for family_id, group in df.groupby("family_id"):
        group = group.sort_values("version_seq")
        latest = group.iloc[-1]
        earliest = group.iloc[0]

        row = {
            "family_id": family_id,
            "family_display_name": latest.get("family_display_name"),
            "family_utility_name": latest.get("family_utility_name"),
            "current_label": latest["label"],
            "current_name": latest.get("name"),
            "current_startdate": latest["startdate"],
            "earliest_available_startdate": earliest["startdate"],
            "baseline_label": None,
            "baseline_name": None,
            "baseline_startdate": None,
            "name_changed_over_window": None,
            "actual_years_spanned": None,
            cagr_col: None,
            review_col: False,
        }

        if pd.isna(latest["startdate"]):
            row["status"] = "excluded"
            row["reason"] = "current version has no startdate"
            results.append(row)
            continue

        years_available = None
        if not pd.isna(earliest["startdate"]):
            years_available = (latest["startdate"] - earliest["startdate"]).days / 365.25
        row["years_of_available_history"] = years_available

        target_date = latest["startdate"] - pd.DateOffset(years=years)
        candidates = group[group["startdate"] <= target_date]

        if candidates.empty:
            row["status"] = "excluded"
            row["reason"] = (
                f"only {years_available:.1f} years of history in the source data "
                f"(need {years}) — the earlier version this would compare against "
                f"either doesn't exist yet or its supersedes chain doesn't reach "
                f"back that far in this dataset"
                if years_available is not None
                else "no earlier version with a known start date"
            )
            results.append(row)
            continue

        baseline = candidates.iloc[-1]  # closest one at/before target date
        actual_years = (latest["startdate"] - baseline["startdate"]).days / 365.25
        row["baseline_label"] = baseline["label"]
        row["baseline_name"] = baseline.get("name")
        row["baseline_startdate"] = baseline["startdate"]
        row["name_changed_over_window"] = baseline.get("name") != latest.get("name")
        row["actual_years_spanned"] = actual_years

        current_val = latest.get(value_col)
        baseline_val = baseline.get(value_col)
        if _is_missing(current_val) or _is_missing(baseline_val):
            row["status"] = "excluded"
            row["reason"] = f"{value_col} is missing for the current or baseline version (no matching rate structure)"
            results.append(row)
            continue

        window_cagr = cagr(current_val, baseline_val, actual_years)
        row[cagr_col] = window_cagr
        row[review_col] = needs_review(window_cagr)
        row["status"] = "included"
        row["reason"] = "ok"
        results.append(row)

    return pd.DataFrame(results)


# ---------- Entry point ----------

def run(export_path: str, out_dir: str = None):
    out_dir = str(out_dir) if out_dir is not None else str(DEFAULT_OUTPUT_DIR)
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    df = load_export(export_path)
    df = add_family_display_name(df)
    df = add_blended_columns(df)
    df = add_period_over_period_escalation(df)

    df.to_csv(f"{out_dir}/rates_with_blended_and_escalation.csv", index=False)

    # Energy has no flat/non-TOU counterpart in this schema the way demand
    # does — energyratestructure is the only energy rate structure, and
    # blended_rate() already handles the non-TOU case (falls back to a
    # plain average when there's no schedule). So there's just one energy
    # sheet, but three for demand: the TOU component, the flat/seasonal
    # component, and the combined total a customer actually pays.
    trailing_specs = [
        ("blended_energy_rate", "energy"),
        ("blended_demand_rate", "demand_tou"),
        ("blended_flat_demand_rate", "demand_flat"),
        ("blended_total_demand_rate", "demand_total"),
    ]

    written = []
    for years in (5, 10):
        for value_col, file_prefix in trailing_specs:
            trend = trailing_window_escalation(df, years, value_col)
            filename = f"{file_prefix}_escalation_{years}yr.csv"
            trend.to_csv(f"{out_dir}/{filename}", index=False)
            written.append(filename)

    print(f"Done. Read from: {export_path}")
    print(f"Wrote to: {out_dir}")
    print(" - rates_with_blended_and_escalation.csv  (full detail, every version)")
    for f in written:
        print(f" - {f}")


if __name__ == "__main__":
    import sys
    export_path = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_INPUT)
    out_dir = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_OUTPUT_DIR)
    run(export_path, out_dir)
