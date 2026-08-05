-- Schema for the OpenEI rate warehouse.
--
-- Stores the full multi-year history of utility rate structures pulled from OpenEI's
-- Utility Rate Database (URDB), so a specific rate can be queried across a span of years
-- along with its eligibility requirements and every charge type (energy TOU, demand TOU,
-- flat demand, coincident demand, fixed/minimum charges).
--
-- Design notes:
--   * OpenEI issues a new "label" (GUID) each time a rate is re-filed (typically annually).
--     `rates.supersedes` points at the label of the previous version, so a rate's history
--     is a chain of rows linked by that field. It is intentionally NOT a foreign key,
--     because a rate can supersede a label that falls outside whatever slice of data
--     you've chosen to load (e.g. an older year, or a different sector query).
--   * The nested rate-structure fields (energy/demand/flat-demand/coincident rates +
--     their weekday/weekend schedules) are stored as JSONB in the exact shape OpenEI
--     returns them. This mirrors what RateSchedule already expects as input (see
--     openei_rates/rateschedule.py), so a stored row can be fed straight back into
--     RateSchedule(row_dict) to reuse all of the existing charge-calculation logic
--     instead of re-deriving a bespoke relational structure for tiers/periods.
--   * `raw` keeps the full, untouched OpenEI response as a safety net/audit trail in
--     case a field gets added upstream that this schema doesn't have a column for yet.

CREATE TABLE IF NOT EXISTS utilities (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,      -- exact name as OpenEI/URDB knows it
    state       TEXT,                      -- curated manually; OpenEI doesn't expose this directly
    eia_id      INTEGER,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rates (
    label                       TEXT PRIMARY KEY,          -- OpenEI page label / GUID
    utility_id                  INTEGER NOT NULL REFERENCES utilities(id),
    utility_name                TEXT NOT NULL,             -- denormalized for convenience/debugging
    name                        TEXT NOT NULL,             -- rate name, e.g. "B-10 Medium General Demand Service..."
    sector                      TEXT,
    servicetype                 TEXT,
    description                 TEXT,
    source                      TEXT,
    source_parent_uri           TEXT,
    openei_uri                  TEXT,
    approved                    BOOLEAN NOT NULL DEFAULT FALSE,
    is_default                  BOOLEAN NOT NULL DEFAULT FALSE,

    -- Versioning / lineage. See note above on why this isn't a hard FK.
    supersedes                  TEXT,

    startdate                   TIMESTAMPTZ,
    enddate                     TIMESTAMPTZ,

    -- Eligibility requirements: whether a customer's demand/usage/voltage/wiring
    -- qualifies them for this rate. NULL/0 means "no limit", matching OpenEI's convention.
    peak_kw_capacity_min        NUMERIC,
    peak_kw_capacity_max        NUMERIC,
    demand_units                TEXT,
    peak_kw_capacity_history    NUMERIC,
    peak_kwh_usage_min          NUMERIC,
    peak_kwh_usage_max          NUMERIC,
    peak_kwh_usage_history      NUMERIC,
    voltage_minimum             NUMERIC,
    voltage_maximum             NUMERIC,
    voltage_category            TEXT,
    phase_wiring                TEXT,

    -- Fixed / minimum charges
    fixed_charge_first_meter    NUMERIC,
    fixed_charge_ea_addl        NUMERIC,
    fixed_charge_units          TEXT,
    min_charge                  NUMERIC,
    min_charge_units            TEXT,
    demand_window_minutes       NUMERIC,

    -- Rate structures, stored exactly as OpenEI returns them.
    energyratestructure         JSONB,
    energyweekdayschedule       JSONB,
    energyweekendschedule       JSONB,

    demandratestructure         JSONB,
    demandweekdayschedule       JSONB,
    demandweekendschedule       JSONB,
    demandratchetpercentage     JSONB,

    flatdemandstructure         JSONB,
    flatdemandmonths            JSONB,

    coincidentratestructure     JSONB,
    coincidentrateschedule      JSONB,

    fueladjustmentsmonthly      JSONB,

    -- Full, untouched OpenEI payload for this rate.
    raw                         JSONB NOT NULL,

    fetched_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rates_utility_sector_start ON rates (utility_id, sector, startdate);
CREATE INDEX IF NOT EXISTS idx_rates_name ON rates (utility_id, name, startdate);
CREATE INDEX IF NOT EXISTS idx_rates_supersedes ON rates (supersedes);
CREATE INDEX IF NOT EXISTS idx_rates_eligibility ON rates (peak_kw_capacity_min, peak_kw_capacity_max, peak_kwh_usage_min, peak_kwh_usage_max);
