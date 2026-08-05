"""Query layer for the OpenEI rate warehouse.

Lets you look up a specific rate's history across years, or find which rates a customer
with a given demand/usage profile would qualify for in a given year - and for either case,
get back a fully usable RateSchedule (reusing openei_rates.rateschedule.RateSchedule, so all
TOU energy/demand, flat demand, coincident demand, and fixed-charge calculations already
built into this package work unmodified) plus the eligibility requirements for that rate-year.

Note: written and syntax-checked, but not run against a live database - see
openei_rates/warehouse/README.md.
"""

import datetime

from ..rateschedule import RateSchedule

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover
    psycopg2 = None


ELIGIBILITY_COLUMNS = [
    'peak_kw_capacity_min', 'peak_kw_capacity_max', 'demand_units', 'peak_kw_capacity_history',
    'peak_kwh_usage_min', 'peak_kwh_usage_max', 'peak_kwh_usage_history',
    'voltage_minimum', 'voltage_maximum', 'voltage_category', 'phase_wiring',
]


def _cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _eligibility(row: dict):
    return {col: row.get(col) for col in ELIGIBILITY_COLUMNS}


def get_rate_history(conn, utility_name: str, rate_name: str, sector: str = None):
    """Returns every year's version of a rate (matched by utility + rate name), oldest first,
    each with its eligibility requirements and effective date range.

    Rate names are matched exactly against what's stored (which is what OpenEI calls it) -
    utilities occasionally do rename a rate across years, in which case this may not catch
    every version; cross-check against the `supersedes` chain (also returned) if a rate's
    name changed.

    :return:    A list of dicts: {label, startdate, enddate, supersedes, eligibility}.
    """
    sql = """
        SELECT r.label, r.startdate, r.enddate, r.supersedes, r.sector, r.approved, r.is_default,
               {eligibility_cols}
        FROM rates r
        JOIN utilities u ON u.id = r.utility_id
        WHERE u.name = %(utility_name)s
          AND r.name = %(rate_name)s
          {sector_clause}
        ORDER BY r.startdate ASC
    """.format(
        eligibility_cols=', '.join('r.{0}'.format(c) for c in ELIGIBILITY_COLUMNS),
        sector_clause='AND r.sector = %(sector)s' if sector else '',
    )

    params = {'utility_name': utility_name, 'rate_name': rate_name}
    if sector:
        params['sector'] = sector

    with _cursor(conn) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        {
            'label': row['label'],
            'startdate': row['startdate'],
            'enddate': row['enddate'],
            'supersedes': row['supersedes'],
            'sector': row['sector'],
            'approved': row['approved'],
            'is_default': row['is_default'],
            'eligibility': _eligibility(row),
        }
        for row in rows
    ]


def get_rate_for_year(conn, utility_name: str, rate_name: str, year: int, sector: str = None):
    """Finds the version of a rate that was in effect during the given calendar year, and
    returns its eligibility requirements plus a RateSchedule for computing charges.

    If a rate changed mid-year, this returns whichever version was in effect at the start
    of that year (Jan 1). Call get_rate_history() directly if you need to handle a
    mid-year rate change explicitly.

    :return:    A dict {label, startdate, enddate, eligibility, rate_schedule} or None if
                no version of this rate was in effect that year.
    """
    year_start = datetime.datetime(year, 1, 1)

    sql = """
        SELECT r.label, r.startdate, r.enddate, r.raw, {eligibility_cols}
        FROM rates r
        JOIN utilities u ON u.id = r.utility_id
        WHERE u.name = %(utility_name)s
          AND r.name = %(rate_name)s
          AND r.startdate <= %(year_start)s
          AND (r.enddate IS NULL OR r.enddate > %(year_start)s)
          {sector_clause}
        ORDER BY r.startdate DESC
        LIMIT 1
    """.format(
        eligibility_cols=', '.join('r.{0}'.format(c) for c in ELIGIBILITY_COLUMNS),
        sector_clause='AND r.sector = %(sector)s' if sector else '',
    )

    params = {'utility_name': utility_name, 'rate_name': rate_name, 'year_start': year_start}
    if sector:
        params['sector'] = sector

    with _cursor(conn) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()

    if not row:
        return None

    return {
        'label': row['label'],
        'startdate': row['startdate'],
        'enddate': row['enddate'],
        'eligibility': _eligibility(row),
        # `raw` was stored as the exact dict OpenEI returned for this rate, so it can be
        # fed straight back into RateSchedule - same parsing path as a fresh API call.
        'rate_schedule': RateSchedule(row['raw']),
    }


def find_qualifying_rates(
        conn,
        utility_name: str,
        year: int,
        sector: str = None,
        demand_kw: float = None,
        usage_kwh: float = None,
    ):
    """Finds every rate a utility offered in a given year that a customer with the given
    peak demand (kW) and/or usage (kWh) would qualify for, based on each rate's
    peak_kw_capacity_min/max and peak_kwh_usage_min/max eligibility fields.

    A rate's min/max bound is treated as "no limit" when it's NULL or 0, matching how
    OpenEI represents unbounded fields (see Rate.qualifies() for the same logic applied
    to a single in-memory Rate object).

    :return:    A list of dicts, one per matching rate: {label, name, eligibility, rate_schedule}.
    """
    year_start = datetime.datetime(year, 1, 1)

    clauses = [
        'u.name = %(utility_name)s',
        'r.startdate <= %(year_start)s',
        '(r.enddate IS NULL OR r.enddate > %(year_start)s)',
    ]
    params = {'utility_name': utility_name, 'year_start': year_start}

    if sector:
        clauses.append('r.sector = %(sector)s')
        params['sector'] = sector

    if demand_kw is not None:
        clauses.append('(r.peak_kw_capacity_min IS NULL OR r.peak_kw_capacity_min = 0 OR r.peak_kw_capacity_min <= %(demand_kw)s)')
        clauses.append('(r.peak_kw_capacity_max IS NULL OR r.peak_kw_capacity_max = 0 OR r.peak_kw_capacity_max >= %(demand_kw)s)')
        params['demand_kw'] = demand_kw

    if usage_kwh is not None:
        clauses.append('(r.peak_kwh_usage_min IS NULL OR r.peak_kwh_usage_min = 0 OR r.peak_kwh_usage_min <= %(usage_kwh)s)')
        clauses.append('(r.peak_kwh_usage_max IS NULL OR r.peak_kwh_usage_max = 0 OR r.peak_kwh_usage_max >= %(usage_kwh)s)')
        params['usage_kwh'] = usage_kwh

    sql = """
        SELECT r.label, r.name, r.raw, {eligibility_cols}
        FROM rates r
        JOIN utilities u ON u.id = r.utility_id
        WHERE {where_clause}
        ORDER BY r.name ASC
    """.format(
        eligibility_cols=', '.join('r.{0}'.format(c) for c in ELIGIBILITY_COLUMNS),
        where_clause=' AND '.join(clauses),
    )

    with _cursor(conn) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        {
            'label': row['label'],
            'name': row['name'],
            'eligibility': _eligibility(row),
            'rate_schedule': RateSchedule(row['raw']),
        }
        for row in rows
    ]
