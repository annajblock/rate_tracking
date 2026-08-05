# OpenEI Rate Warehouse

Pulls utility rate structures from OpenEI's Utility Rate Database (URDB) into Postgres, so
you can query a specific rate's eligibility requirements and full charge structure (TOU
energy, TOU demand, flat demand, coincident demand, fixed/minimum charges) across a span
of years, instead of hitting the live API every time.

## How rate history works in OpenEI

OpenEI doesn't store "one rate with a date range" - each time a utility re-files a rate
(typically annually), OpenEI creates a brand-new page/label (GUID) for it, and sets that
new rate's `supersedes` field to the label of the version it replaced. So a rate's 10-year
history is a chain of separate records. The `ratesforutility` API parameter returns every
label a utility has ever had on file (all sectors, all years), which is what the ETL relies
on - it does not need to walk the `supersedes` chain manually, though that field is stored
so you can verify/trace lineage.

## Setup

```bash
pip install psycopg2-binary   # not in the base package requirements.txt - only needed here

export DATABASE_URL=postgresql://user:pass@host:5432/dbname
export OPENEI_API_KEY=your_key_here   # free key: https://openei.org/services/api/signup/
```

Run the sync (creates the schema on first run, then upserts):

```bash
python3 -m openei_rates.warehouse.etl
```

This pulls Commercial + Industrial rates for the utilities listed in `ca_utilities.py`
(a curated starter list of major California IOUs and municipal utilities). Edit that list
to add/remove utilities, or call `sync_utility()` / `sync_all()` directly for more control:

```python
from openei_rates.warehouse.etl import get_connection, ensure_schema, sync_all

# everything, using env vars for connection + key
sync_all()

# or, more control:
from openei_rates.openei_rates import OpenEIRates
from openei_rates.warehouse.etl import sync_utility, get_connection, ensure_schema

conn = get_connection()
ensure_schema(conn)
eir = OpenEIRates('your_key_here')
sync_utility(conn, eir, 'Pacific Gas & Electric Co', sectors=['Commercial'], state='CA')
```

## Querying

```python
from openei_rates.warehouse.query import get_rate_for_year, get_rate_history, find_qualifying_rates

# One rate, one year - eligibility + a ready-to-use RateSchedule for computing charges
result = get_rate_for_year(conn, 'Pacific Gas & Electric Co', 'B-10 Medium General Demand Service (Secondary Voltage)', 2021)
result['eligibility']       # {'peak_kw_capacity_min': 75.0, 'peak_kw_capacity_max': 999.0, ...}
result['rate_schedule']     # a RateSchedule - call .get_costs(demand_series) same as anywhere else in this package

# Full year-by-year history of one rate
history = get_rate_history(conn, 'Pacific Gas & Electric Co', 'B-10 Medium General Demand Service (Secondary Voltage)')

# Which rates would a customer with this profile have qualified for in 2021?
matches = find_qualifying_rates(conn, 'Pacific Gas & Electric Co', 2021, sector='Commercial', demand_kw=150, usage_kwh=45000)
```

`get_rate_for_year`/`find_qualifying_rates` reconstruct a `RateSchedule` directly from the
stored raw OpenEI payload (JSONB column), so all of the existing cost-calculation code in
`openei_rates/rateschedule.py` and `openei_rates/helpers/` works unmodified - the warehouse
is purely a caching/query layer on top of it, not a reimplementation.

## Important: this was built without live network or Postgres access

The sandbox this was developed in has no outbound network access to OpenEI's API or apt/pip
package mirrors, and no Postgres instance available, so **none of this has been run against
real data or a real database.** What was verified instead:

- `openei_rates/warehouse/*.py` and the modified `rate.py`/`openei_rates.py`/`rateschedule.py`
  all compile cleanly.
- `tests/test_warehouse.py` exercises `Rate` field parsing, eligibility logic, `RateSchedule`'s
  field-name fixes, and `OpenEIRates.get_rates_for_utility`'s pagination - all against
  synthetic fixtures shaped exactly like OpenEI's documented API response, with no network
  calls. All 14 tests pass (`python3 -m unittest tests.test_warehouse -v`; requires numba
  and, for the ETL row-mapping test, psycopg2 to be installed in your real environment).
- The `schema.sql` DDL and the SQL in `query.py`/`etl.py` were hand-reviewed against
  Postgres syntax but never executed against a live Postgres server.

Before relying on this, run `sync_utility()` for one utility/sector, spot-check a couple of
rows against the same rate's page on https://apps.openei.org/USURDB/, and run
`get_rate_for_year()` + `.get_costs()` against a known billing period to confirm the
computed charges match a real bill (similar to how `tests/test_rateschedule.py`'s
`test_energy_cost` sets up an expected `total` but - worth noting - never actually asserts
against it; wiring up that assertion, or adding an equivalent one here, is the strongest
next step to genuinely confirm correctness rather than just "it ran without crashing").

## Field-name bugs found and fixed while building this

While tracing `RateSchedule.__init__` to confirm it maps every field this warehouse
stores, three key-name mismatches against OpenEI's actual API field names were found and
fixed (all previously fell back to defaults on every real rate, silently):

- `demandratewindow` -> `demandwindow` (demand window minutes; was always defaulting to 15)
- `demandrachetpercentage` -> `demandratchetpercentage` (was always zeros)
- `fixedmonthlycharge` -> `fixedchargefirstmeter` (fixed monthly charge; was always $0)

The third one is worth flagging specifically if you've looked at cost output before this
fix and the `fixed_cost` column was always 0 - that wasn't the rate having no fixed charge,
it was this bug.
