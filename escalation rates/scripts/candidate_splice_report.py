"""
Candidate Splice Report
------------------------
Finds places where a tariff's supersedes chain dead-ends, and a plausible
successor exists elsewhere in the same utility's data with no structural
link back to it (e.g. PG&E B-19 Primary Mandatory starting fresh in 2022
with no predecessor, or SCE TOU-GS-2 CPP dead-ending in 2016 right as
Option A/B/D/E/R appear as brand-new roots).

This is DELIBERATELY conservative and separate from the main escalation
pipeline: it never modifies family_id, version_seq, or any CAGR value.
It only produces a list of candidate (dead_end -> candidate_successor)
pairs for manual review. Nothing here should be treated as fact until a
human confirms it.

Matching logic (structural only, in this pass -- no text/comments signal
yet, see note at bottom of file):
  1. A "dead end" is a family's most recent version (max version_seq) --
     nothing in the dataset points back to it via supersedes.
  2. A "root" is any version_seq == 0 row (no supersedes) -- a family
     with no known predecessor.
  3. A base schedule code is extracted from the name (e.g. "B-19",
     "GS-2", "PA-3") via regex. Dead-ends and roots are only paired if
     they share a base code AND belong to the same utility_id AND the
     root's startdate comes after the dead-end's startdate within
     MAX_GAP_YEARS.
  4. sector_match / servicetype_match are reported as soft signals, not
     hard filters (real data shows these can legitimately differ across
     a redesign, e.g. servicetype null vs "Delivery with Standard Offer"
     within the same true lineage).

Repo layout this script expects (paths below are resolved relative to this
file's own location, not your current working directory):
    inputs/   <- source JSON export goes here
    outputs/  <- candidate_splice_report.csv lands here
    scripts/  <- this file

Usage:
    python candidate_splice_report.py                              # uses defaults below
    python candidate_splice_report.py path/to/export.json
    python candidate_splice_report.py path/to/export.json path/to/output.csv
"""

import re
import sys
import json
from pathlib import Path

import pandas as pd

MAX_GAP_YEARS = 4  # how long after a dead-end we'll still consider a root a plausible successor
MIN_GAP_YEARS = 0.08  # ~1 month -- same-day/same-week "successors" are almost always
# parallel sibling options launched together (e.g. Option A/B/CPP/R all introduced at
# once as customer choices), not a real predecessor -> successor relationship

BASE_CODE_PATTERN = re.compile(r"\b[A-Z]{1,5}-\d+[A-Z]?\b")

# Resolved relative to this file, not the caller's cwd.
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_INPUT = REPO_ROOT / "inputs" / "sql_output_w_descriptions.json"
DEFAULT_OUTPUT = REPO_ROOT / "outputs" / "candidate_splice_report.csv"


def load(path: str) -> pd.DataFrame:
    with open(path, "r") as f:
        raw = json.load(f)
    if isinstance(raw, dict) and len(raw) == 1:
        raw = next(iter(raw.values()))
    df = pd.DataFrame(raw)
    for date_col in ["startdate", "enddate", "prior_startdate"]:
        if date_col in df.columns:
            df[date_col] = pd.to_datetime(df[date_col], utc=True, errors="coerce")
    return df


def extract_base_code(name):
    if not name:
        return None
    matches = BASE_CODE_PATTERN.findall(name)
    return matches[0] if matches else None


def build_candidate_report(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["family_id", "version_seq"]).copy()
    df["base_code"] = df["name"].apply(extract_base_code)

    dead_ends = df.groupby("family_id").tail(1).copy()
    roots = df[df["version_seq"] == 0].copy()

    no_code_dead_ends = dead_ends["base_code"].isna().sum()
    no_code_roots = roots["base_code"].isna().sum()

    results = []
    for _, de in dead_ends.iterrows():
        if pd.isna(de["base_code"]) or pd.isna(de["startdate"]):
            continue

        candidates = roots[
            (roots["utility_id"] == de["utility_id"])
            & (roots["base_code"] == de["base_code"])
            & (roots["family_id"] != de["family_id"])
            & (roots["startdate"] > de["startdate"])
        ].copy()
        if candidates.empty:
            continue

        candidates["date_gap_years"] = (candidates["startdate"] - de["startdate"]).dt.days / 365.25
        candidates = candidates[
            (candidates["date_gap_years"] <= MAX_GAP_YEARS)
            & (candidates["date_gap_years"] >= MIN_GAP_YEARS)
        ]
        if candidates.empty:
            continue

        for _, cand in candidates.sort_values("date_gap_years").iterrows():
            sector_match = de.get("sector") == cand.get("sector")
            servicetype_match = de.get("servicetype") == cand.get("servicetype")
            match_strength = "strong" if (sector_match and servicetype_match) else "weak"

            results.append({
                "base_code": de["base_code"],
                "utility_name": de.get("utility_name"),
                "dead_end_family_id": de["family_id"],
                "dead_end_label": de["label"],
                "dead_end_name": de.get("name"),
                "dead_end_last_startdate": de["startdate"],
                "dead_end_sector": de.get("sector"),
                "dead_end_servicetype": de.get("servicetype"),
                "candidate_family_id": cand["family_id"],
                "candidate_label": cand["label"],
                "candidate_name": cand.get("name"),
                "candidate_startdate": cand["startdate"],
                "candidate_sector": cand.get("sector"),
                "candidate_servicetype": cand.get("servicetype"),
                "date_gap_years": round(cand["date_gap_years"], 2),
                "sector_match": sector_match,
                "servicetype_match": servicetype_match,
                "match_strength": match_strength,
                "confirmed": "",  # <- fill in TRUE/FALSE yourself after review
                "notes": "",      # <- your notes go here
            })

    report = pd.DataFrame(results)
    if not report.empty:
        report = report.sort_values(
            ["dead_end_family_id", "match_strength", "date_gap_years"],
            ascending=[True, True, True],
        )

    print(f"Dead-end families with no extractable base code (skipped): {no_code_dead_ends}")
    print(f"Root families with no extractable base code (skipped): {no_code_roots}")
    print(f"Total dead-end families examined: {len(dead_ends)}")
    print(f"Candidate pairs found: {len(report)}")
    if not report.empty:
        print(f"  strong matches: {(report['match_strength'] == 'strong').sum()}")
        print(f"  weak matches:   {(report['match_strength'] == 'weak').sum()}")
        print(f"Distinct dead-ends with at least one candidate: {report['dead_end_label'].nunique()}")

    return report


if __name__ == "__main__":
    export_path = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_INPUT)
    output_path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_OUTPUT)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    df = load(export_path)
    report = build_candidate_report(df)
    report.to_csv(output_path, index=False)
    print(f"\nWrote {output_path}")

# NOTE ON TEXT CORROBORATION (not yet implemented):
# You found that `raw` contains a `basicinformationcomments` key that holds
# the kind of "prior customers on Option A will be transitioned to Option E"
# language the OpenEI website shows under "Basic Comments". That text isn't
# in this export yet -- only `description` is, and it turned out to be
# generic boilerplate, not transition history. Once we add r.raw (or better,
# a direct extraction like r.raw::jsonb ->> 'basicinformationcomments') to
# the SQL export, this script can cross-check each candidate pair: if either
# side's comments text mentions the other side's name/option, that's strong
# corroborating evidence and should bump match_strength up regardless of the
# structural sector/servicetype signals.
