"""ETL: pull utility rate histories from OpenEI and load them into the Postgres warehouse
defined in schema.sql.

Usage (as a script):

    DATABASE_URL=postgresql://user:pass@host:5432/dbname \\
    OPENEI_API_KEY=your_key_here \\
    python3 -m openei_rates.warehouse.etl

Or import and call `sync_utility(...)` / `sync_all(...)` directly.

Requires `psycopg2` (or `psycopg2-binary`), which is NOT in this package's base
requirements.txt since it's only needed if you're using the warehouse. Install it
separately: `pip install psycopg2-binary`.

Note: this module was written and syntax-checked, but not run end-to-end against a live
Postgres instance or the live OpenEI API - the sandbox this was built in has no outbound
network access to either. Test it against a real database before relying on it; see
openei_rates/warehouse/README.md.
"""

import datetime
import os

from .. import logger
from ..openei_rates import OpenEIRates
from ..rate import Rate
from .ca_utilities import CALIFORNIA_UTILITIES, SECTORS

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover - only needed when actually running the ETL
    psycopg2 = None


SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'schema.sql')


def get_connection(database_url: str = None):
    """Opens a psycopg2 connection using DATABASE_URL (env var if not passed explicitly)."""
    if psycopg2 is None:
        raise ImportError(
            'psycopg2 is required for the warehouse ETL. Install it with: '
            'pip install psycopg2-binary'
        )
    database_url = database_url or os.environ.get('DATABASE_URL')
    if not database_url:
        raise ValueError(
            'No database URL supplied. Set the DATABASE_URL environment variable '
            '(postgresql://user:pass@host:5432/dbname) or pass database_url explicitly.'
        )
    return psycopg2.connect(database_url)


def ensure_schema(conn):
    """Applies schema.sql. Safe to run repeatedly - everything in it is CREATE ... IF NOT EXISTS."""
    with open(SCHEMA_PATH, 'r') as f:
        ddl = f.read()
    with conn.cursor() as cur:
        cur.execute(ddl)
    conn.commit()


def _get_or_create_utility(conn, utility_name: str, state: str = None):
    with conn.cursor() as cur:
        cur.execute('SELECT id FROM utilities WHERE name = %s', (utility_name,))
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            'INSERT INTO utilities (name, state) VALUES (%s, %s) RETURNING id',
            (utility_name, state),
        )
        utility_id = cur.fetchone()[0]
    conn.commit()
    return utility_id


def _ts(dt: datetime.datetime):
    return dt if dt else None


def _rate_to_row(rate: Rate, utility_id: int):
    """Maps a Rate object (plus its raw dict) to a dict of column -> value for the
    `rates` table upsert.
    """
    raw = rate.raw or {}

    return {
        'label': rate.label,
        'utility_id': utility_id,
        'utility_name': rate.utility,
        'name': rate.name,
        'sector': rate.sector,
        'servicetype': rate.servicetype,
        'description': rate.description,
        'source': rate.source,
        'source_parent_uri': rate.source_parent_uri,
        'openei_uri': rate.openei_uri,
        'approved': bool(rate.approved),
        'is_default': bool(rate.is_default),
        'supersedes': rate.supersedes,
        'startdate': _ts(rate.begin_date),
        'enddate': _ts(rate.end_date),

        'peak_kw_capacity_min': rate.peak_kw_capacity_min,
        'peak_kw_capacity_max': rate.peak_kw_capacity_max,
        'demand_units': rate.demand_units,
        'peak_kw_capacity_history': rate.peak_kw_capacity_history,
        'peak_kwh_usage_min': rate.peak_kwh_usage_min,
        'peak_kwh_usage_max': rate.peak_kwh_usage_max,
        'peak_kwh_usage_history': rate.peak_kwh_usage_history,
        'voltage_minimum': rate.voltage_minimum,
        'voltage_maximum': rate.voltage_maximum,
        'voltage_category': rate.voltage_category,
        'phase_wiring': rate.wiring,

        'fixed_charge_first_meter': rate.fixed_charge_first_meter,
        'fixed_charge_ea_addl': rate.fixed_charge_ea_addl,
        'fixed_charge_units': rate.fixed_charge_units,
        'min_charge': rate.min_charge,
        'min_charge_units': rate.min_charge_units,
        'demand_window_minutes': raw.get('demandwindow'),

        'energyratestructure': psycopg2.extras.Json(raw.get('energyratestructure')) if raw.get('energyratestructure') is not None else None,
        'energyweekdayschedule': psycopg2.extras.Json(raw.get('energyweekdayschedule')) if raw.get('energyweekdayschedule') is not None else None,
        'energyweekendschedule': psycopg2.extras.Json(raw.get('energyweekendschedule')) if raw.get('energyweekendschedule') is not None else None,

        'demandratestructure': psycopg2.extras.Json(raw.get('demandratestructure')) if raw.get('demandratestructure') is not None else None,
        'demandweekdayschedule': psycopg2.extras.Json(raw.get('demandweekdayschedule')) if raw.get('demandweekdayschedule') is not None else None,
        'demandweekendschedule': psycopg2.extras.Json(raw.get('demandweekendschedule')) if raw.get('demandweekendschedule') is not None else None,
        'demandratchetpercentage': psycopg2.extras.Json(raw.get('demandratchetpercentage')) if raw.get('demandratchetpercentage') is not None else None,

        'flatdemandstructure': psycopg2.extras.Json(raw.get('flatdemandstructure')) if raw.get('flatdemandstructure') is not None else None,
        'flatdemandmonths': psycopg2.extras.Json(raw.get('flatdemandmonths')) if raw.get('flatdemandmonths') is not None else None,

        'coincidentratestructure': psycopg2.extras.Json(raw.get('coincidentratestructure')) if raw.get('coincidentratestructure') is not None else None,
        'coincidentrateschedule': psycopg2.extras.Json(raw.get('coincidentrateschedule')) if raw.get('coincidentrateschedule') is not None else None,

        'fueladjustmentsmonthly': psycopg2.extras.Json(raw.get('fueladjustmentsmonthly')) if raw.get('fueladjustmentsmonthly') is not None else None,

        'raw': psycopg2.extras.Json(raw),
    }


_UPSERT_SQL = """
INSERT INTO rates (
    label, utility_id, utility_name, name, sector, servicetype, description, source,
    source_parent_uri, openei_uri, approved, is_default, supersedes, startdate, enddate,
    peak_kw_capacity_min, peak_kw_capacity_max, demand_units, peak_kw_capacity_history,
    peak_kwh_usage_min, peak_kwh_usage_max, peak_kwh_usage_history,
    voltage_minimum, voltage_maximum, voltage_category, phase_wiring,
    fixed_charge_first_meter, fixed_charge_ea_addl, fixed_charge_units,
    min_charge, min_charge_units, demand_window_minutes,
    energyratestructure, energyweekdayschedule, energyweekendschedule,
    demandratestructure, demandweekdayschedule, demandweekendschedule, demandratchetpercentage,
    flatdemandstructure, flatdemandmonths,
    coincidentratestructure, coincidentrateschedule,
    fueladjustmentsmonthly, raw
) VALUES (
    %(label)s, %(utility_id)s, %(utility_name)s, %(name)s, %(sector)s, %(servicetype)s, %(description)s, %(source)s,
    %(source_parent_uri)s, %(openei_uri)s, %(approved)s, %(is_default)s, %(supersedes)s, %(startdate)s, %(enddate)s,
    %(peak_kw_capacity_min)s, %(peak_kw_capacity_max)s, %(demand_units)s, %(peak_kw_capacity_history)s,
    %(peak_kwh_usage_min)s, %(peak_kwh_usage_max)s, %(peak_kwh_usage_history)s,
    %(voltage_minimum)s, %(voltage_maximum)s, %(voltage_category)s, %(phase_wiring)s,
    %(fixed_charge_first_meter)s, %(fixed_charge_ea_addl)s, %(fixed_charge_units)s,
    %(min_charge)s, %(min_charge_units)s, %(demand_window_minutes)s,
    %(energyratestructure)s, %(energyweekdayschedule)s, %(energyweekendschedule)s,
    %(demandratestructure)s, %(demandweekdayschedule)s, %(demandweekendschedule)s, %(demandratchetpercentage)s,
    %(flatdemandstructure)s, %(flatdemandmonths)s,
    %(coincidentratestructure)s, %(coincidentrateschedule)s,
    %(fueladjustmentsmonthly)s, %(raw)s
)
ON CONFLICT (label) DO UPDATE SET
    utility_id = EXCLUDED.utility_id,
    utility_name = EXCLUDED.utility_name,
    name = EXCLUDED.name,
    sector = EXCLUDED.sector,
    servicetype = EXCLUDED.servicetype,
    description = EXCLUDED.description,
    source = EXCLUDED.source,
    source_parent_uri = EXCLUDED.source_parent_uri,
    openei_uri = EXCLUDED.openei_uri,
    approved = EXCLUDED.approved,
    is_default = EXCLUDED.is_default,
    supersedes = EXCLUDED.supersedes,
    startdate = EXCLUDED.startdate,
    enddate = EXCLUDED.enddate,
    peak_kw_capacity_min = EXCLUDED.peak_kw_capacity_min,
    peak_kw_capacity_max = EXCLUDED.peak_kw_capacity_max,
    demand_units = EXCLUDED.demand_units,
    peak_kw_capacity_history = EXCLUDED.peak_kw_capacity_history,
    peak_kwh_usage_min = EXCLUDED.peak_kwh_usage_min,
    peak_kwh_usage_max = EXCLUDED.peak_kwh_usage_max,
    peak_kwh_usage_history = EXCLUDED.peak_kwh_usage_history,
    voltage_minimum = EXCLUDED.voltage_minimum,
    voltage_maximum = EXCLUDED.voltage_maximum,
    voltage_category = EXCLUDED.voltage_category,
    phase_wiring = EXCLUDED.phase_wiring,
    fixed_charge_first_meter = EXCLUDED.fixed_charge_first_meter,
    fixed_charge_ea_addl = EXCLUDED.fixed_charge_ea_addl,
    fixed_charge_units = EXCLUDED.fixed_charge_units,
    min_charge = EXCLUDED.min_charge,
    min_charge_units = EXCLUDED.min_charge_units,
    demand_window_minutes = EXCLUDED.demand_window_minutes,
    energyratestructure = EXCLUDED.energyratestructure,
    energyweekdayschedule = EXCLUDED.energyweekdayschedule,
    energyweekendschedule = EXCLUDED.energyweekendschedule,
    demandratestructure = EXCLUDED.demandratestructure,
    demandweekdayschedule = EXCLUDED.demandweekdayschedule,
    demandweekendschedule = EXCLUDED.demandweekendschedule,
    demandratchetpercentage = EXCLUDED.demandratchetpercentage,
    flatdemandstructure = EXCLUDED.flatdemandstructure,
    flatdemandmonths = EXCLUDED.flatdemandmonths,
    coincidentratestructure = EXCLUDED.coincidentratestructure,
    coincidentrateschedule = EXCLUDED.coincidentrateschedule,
    fueladjustmentsmonthly = EXCLUDED.fueladjustmentsmonthly,
    raw = EXCLUDED.raw,
    fetched_at = now();
"""


def sync_utility(
        conn,
        eir: OpenEIRates,
        utility_name: str,
        sectors=SECTORS,
        state: str = None,
        years_back: int = None,
    ):
    """Fetches a utility's full rate history for the given sectors and upserts it into
    the warehouse.

    :param  conn:           An open psycopg2 connection.
    :param  eir:            An OpenEIRates instance (holds the API key).
    :param  utility_name:   Exact utility name as OpenEI has it (see ca_utilities.py notes).
    :param  sectors:        Sectors to pull, e.g. ['Commercial', 'Industrial'].
    :param  state:          Optional state tag to store on the utility row (e.g. 'CA').
    :param  years_back:     If set, only upsert rates whose startdate is within this many
                             years of today. Leave as None to load everything OpenEI returns
                             (recommended - keeps supersedes chains intact even if a linked
                             rate falls just outside the window).
    :return:    The number of rate rows upserted.
    """
    utility_id = _get_or_create_utility(conn, utility_name, state=state)

    cutoff = None
    if years_back is not None:
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=365 * years_back)

    total = 0
    with conn.cursor() as cur:
        for sector in sectors:
            rates = eir.get_rates_for_utility(utility_name, sector=sector)
            logger.info('Fetched {} {} rates for {}'.format(len(rates), sector, utility_name))

            for rate in rates:
                if cutoff and rate.begin_date and rate.begin_date < cutoff:
                    continue
                row = _rate_to_row(rate, utility_id)
                cur.execute(_UPSERT_SQL, row)
                total += 1

    conn.commit()
    return total


def sync_all(
        database_url: str = None,
        api_key: str = None,
        utilities=CALIFORNIA_UTILITIES,
        sectors=SECTORS,
        state: str = 'CA',
        years_back: int = None,
    ):
    """Syncs a list of utilities' rate histories into the warehouse. Entry point used when
    running this module as a script.
    """
    api_key = api_key or os.environ.get('OPENEI_API_KEY')
    if not api_key:
        raise ValueError('No OpenEI API key supplied. Set OPENEI_API_KEY or pass api_key explicitly.')

    eir = OpenEIRates(api_key)
    conn = get_connection(database_url)
    try:
        ensure_schema(conn)
        total = 0
        for utility_name in utilities:
            try:
                total += sync_utility(conn, eir, utility_name, sectors=sectors, state=state, years_back=years_back)
            except Exception:
                logger.exception('Failed to sync utility: {}'.format(utility_name))
        logger.info('Sync complete. {} rate rows upserted.'.format(total))
        return total
    finally:
        conn.close()


if __name__ == '__main__':
    sync_all()
