"""Tests for the Rate/OpenEIRates extensions and the warehouse layer that don't require
network access or a live Postgres instance.

These exist because the sandbox this was developed in has no outbound network access to
OpenEI's API and no Postgres/psycopg2 available to test against - see
openei_rates/warehouse/README.md. Run test_openei_rates.py / test_rateschedule.py
separately (they hit the live API) to validate against real data.
"""

import copy
import datetime
import unittest

from openei_rates.rate import Rate
from openei_rates.rateschedule import RateSchedule
from openei_rates.openei_rates import OpenEIRates

try:
    import psycopg2  # noqa: F401
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


def _ts(dt: datetime.datetime) -> int:
    return int(dt.replace(tzinfo=datetime.timezone.utc).timestamp())


# A synthetic OpenEI rate record covering every field this project reads from it,
# shaped exactly like the real API response (per
# https://apps.openei.org/services/doc/rest/util_rates/?version=7).
SAMPLE_RATE_DICT = {
    'label': 'test_label_2023',
    'uri': 'https://apps.openei.org/USURDB/rate/view/test_label_2023',
    'name': 'B-10 Medium General Demand Service',
    'utility': 'Pacific Gas & Electric Co',
    'sector': 'Commercial',
    'servicetype': 'Bundled',
    'description': 'A synthetic test rate.',
    'source': 'https://example.com/tariff.pdf',
    'sourceparent': 'https://example.com',
    'phasewiring': '3-Phase',
    'eia': 14328,
    'approved': True,
    'is_default': False,
    'supercedes': 'test_label_2022',
    'startdate': _ts(datetime.datetime(2023, 3, 1)),
    'enddate': _ts(datetime.datetime(2024, 3, 1)),

    'peakkwcapacitymin': 75.0,
    'peakkwcapacitymax': 999.0,
    'demandunits': 'kW',
    'peakkwcapacityhistory': 12,
    'peakkwhusagemin': 0,
    'peakkwhusagemax': 0,
    'peakkwhusagehistory': 0,
    'voltageminimum': 2400,
    'voltagemaximum': 50000,
    'voltagecategory': 'Secondary',

    'fixedchargefirstmeter': 245.50,
    'fixedchargeeaaddl': 0,
    'fixedchargeunits': '$/month',
    'mincharge': 0,
    'minchargeunits': '$/month',

    'demandwindow': 15,
    'demandratchetpercentage': [0.5] * 12,

    'demandrateunit': 'kW',
    'demandratestructure': [[{'max': 0, 'rate': 20.3, 'adj': 0}]],
    'demandweekdayschedule': [[0] * 24 for _ in range(12)],
    'demandweekendschedule': [[0] * 24 for _ in range(12)],

    'flatdemandunit': 'kW',
    'flatdemandstructure': [[{'max': 0, 'rate': 18.7, 'adj': 0}]],
    'flatdemandmonths': [0] * 12,

    'energyratestructure': [
        [{'max': 0, 'rate': 0.1338, 'adj': 0, 'unit': 'kWh'}],
        [{'max': 0, 'rate': 0.0969, 'adj': 0, 'unit': 'kWh'}],
        [{'max': 0, 'rate': 0.1611, 'adj': 0, 'unit': 'kWh'}],
    ],
    'energyweekdayschedule': [[0] * 24 for _ in range(12)],
    'energyweekendschedule': [[0] * 24 for _ in range(12)],
}


class TestRateEligibilityFields(unittest.TestCase):
    """Rate should parse every eligibility/versioning field OpenEI provides, not just the
    handful the original class captured."""

    def setUp(self):
        self.rate = Rate(copy.deepcopy(SAMPLE_RATE_DICT))

    def test_versioning_fields(self):
        self.assertEqual(self.rate.label, 'test_label_2023')
        self.assertEqual(self.rate.supersedes, 'test_label_2022')
        self.assertTrue(self.rate.approved)
        self.assertFalse(self.rate.is_default)

    def test_approved_reflects_actual_data_not_hardcoded(self):
        d = copy.deepcopy(SAMPLE_RATE_DICT)
        d['approved'] = False
        rate = Rate(d)
        self.assertFalse(rate.approved, 'approved should come from the API response, not be hardcoded True')

    def test_eligibility_fields_parsed(self):
        self.assertEqual(self.rate.peak_kw_capacity_min, 75.0)
        self.assertEqual(self.rate.peak_kw_capacity_max, 999.0)
        self.assertEqual(self.rate.voltage_category, 'Secondary')

    def test_dates_parsed(self):
        self.assertEqual(self.rate.begin_date.year, 2023)
        self.assertEqual(self.rate.end_date.year, 2024)

    def test_qualifies_within_demand_bounds(self):
        self.assertTrue(self.rate.qualifies(demand_kw=100))
        self.assertFalse(self.rate.qualifies(demand_kw=10), 'below peakkwcapacitymin, should not qualify')
        self.assertFalse(self.rate.qualifies(demand_kw=5000), 'above peakkwcapacitymax, should not qualify')

    def test_qualifies_treats_zero_usage_bounds_as_unbounded(self):
        # peakkwhusagemin/max are both 0 in the fixture, meaning "no limit"
        self.assertTrue(self.rate.qualifies(usage_kwh=1))
        self.assertTrue(self.rate.qualifies(usage_kwh=10_000_000))

    def test_qualifies_with_no_args_is_always_true(self):
        self.assertTrue(self.rate.qualifies())


class TestRateScheduleFieldMapping(unittest.TestCase):
    """Regression tests for three key-name bugs found while building the warehouse:
    RateSchedule was reading 'demandratewindow', 'demandrachetpercentage', and
    'fixedmonthlycharge' - none of which are real OpenEI field names - so these always
    silently fell back to defaults on real data. Confirms the corrected field names
    ('demandwindow', 'demandratchetpercentage', 'fixedchargefirstmeter') are now read.
    """

    def setUp(self):
        self.rs = RateSchedule(copy.deepcopy(SAMPLE_RATE_DICT))

    def test_demand_window_read_from_correct_key(self):
        self.assertEqual(self.rs.demand_window, 15)

    def test_fixed_monthly_charge_read_from_correct_key(self):
        self.assertEqual(self.rs.fixed_monthly_charge, 245.50)

    def test_demand_ratchet_pct_read_from_correct_key(self):
        self.assertAlmostEqual(float(self.rs.demand_ratchet_pct[0]), 0.5)

    def test_falls_back_sanely_when_fields_missing(self):
        d = copy.deepcopy(SAMPLE_RATE_DICT)
        del d['demandwindow']
        del d['fixedchargefirstmeter']
        rs = RateSchedule(d)
        self.assertEqual(rs.demand_window, RateSchedule.default_demand_window)
        self.assertEqual(rs.fixed_monthly_charge, 0)


class TestGetRatesForUtilityPagination(unittest.TestCase):
    """get_rates_for_utility should page through results without hitting the network,
    by exercising it against a stubbed OpenEIApi.rate_query.
    """

    def _make_item(self, i):
        d = copy.deepcopy(SAMPLE_RATE_DICT)
        d['label'] = 'label_{}'.format(i)
        return d

    def test_stops_on_short_page(self):
        eir = OpenEIRates('fake-key-not-used')

        pages = [
            [self._make_item(i) for i in range(3)],  # full page (page_size=3 below)
            [self._make_item(i) for i in range(3, 5)],  # short page -> stop here
        ]
        call_log = []

        def fake_rate_query(params):
            call_log.append(dict(params))
            if not pages:
                return (200, [])
            return (200, pages.pop(0))

        eir.api.rate_query = fake_rate_query

        rates = eir.get_rates_for_utility('Pacific Gas & Electric Co', sector='Commercial', page_size=3)

        self.assertEqual(len(rates), 5)
        self.assertEqual([r.label for r in rates], ['label_0', 'label_1', 'label_2', 'label_3', 'label_4'])
        # Exactly two pages should have been requested (page 1 full, page 2 short -> stop).
        self.assertEqual(len(call_log), 2)
        self.assertEqual(call_log[0]['offset'], 0)
        self.assertEqual(call_log[1]['offset'], 3)
        self.assertEqual(call_log[0]['sector'], 'Commercial')
        self.assertEqual(call_log[0]['ratesforutility'], 'Pacific Gas & Electric Co')

    def test_stops_on_empty_response(self):
        eir = OpenEIRates('fake-key-not-used')
        eir.api.rate_query = lambda params: (404, None)

        rates = eir.get_rates_for_utility('Nonexistent Utility Co')
        self.assertEqual(rates, [])


class TestGetRateByLabelRequestsFullDetail(unittest.TestCase):
    """Regression test: get_rate_by_label (and therefore get_rate_by_url) used to fetch
    without detail=full, which per OpenEI's API means 'minimal' mode - only fields tied
    to the request params come back, so eligibility fields, supersedes, structures, etc.
    were silently missing from every Rate built this way, even though Rate itself parses
    them correctly when they're present. Confirms detail=full is now always requested.
    """

    def test_requests_full_detail(self):
        eir = OpenEIRates('fake-key-not-used')
        seen_params = {}

        def fake_rate_query(params):
            seen_params.update(params)
            return (200, [copy.deepcopy(SAMPLE_RATE_DICT)])

        eir.api.rate_query = fake_rate_query

        rate = eir.get_rate_by_label('test_label_2023')

        self.assertEqual(seen_params.get('detail'), 'full')
        self.assertIsNotNone(rate)
        self.assertEqual(rate.peak_kw_capacity_max, 999.0, 'eligibility fields should be populated when detail=full is actually sent')


@unittest.skipUnless(HAS_PSYCOPG2, 'psycopg2 not installed in this environment')
class TestEtlRowMapping(unittest.TestCase):
    """Only runs where psycopg2 is actually installed (it isn't in the sandbox this was
    built in). Verifies _rate_to_row doesn't blow up and maps the fields it should.
    """

    def test_rate_to_row_maps_core_fields(self):
        from openei_rates.warehouse.etl import _rate_to_row

        rate = Rate(copy.deepcopy(SAMPLE_RATE_DICT))
        row = _rate_to_row(rate, utility_id=1)

        self.assertEqual(row['label'], 'test_label_2023')
        self.assertEqual(row['utility_id'], 1)
        self.assertEqual(row['supersedes'], 'test_label_2022')
        self.assertEqual(row['peak_kw_capacity_min'], 75.0)
        self.assertIsNotNone(row['raw'])


if __name__ == '__main__':
    unittest.main()
