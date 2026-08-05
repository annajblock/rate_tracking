import datetime
from .api import OpenEIApi
from .rateschedule import RateSchedule

class Rate(object):
    """A Rate object holds metadata about a rate. It pulls down a new RateSchedule only when needed.

    In addition to basic identifying fields, this also captures the fields needed to track a rate's
    version history (``supersedes``) and the eligibility requirements that determine whether a given
    customer qualifies for the rate (demand/usage minimums and maximums, voltage, phase wiring).
    See https://apps.openei.org/services/doc/rest/util_rates/?version=7 for field definitions.
    """

    def __init__(self, d: dict):

        # Identity
        self.sector = d.get('sector')
        self.servicetype = d.get('servicetype')
        self.approved = bool(d.get('approved', False))
        self.is_default = bool(d.get('is_default', False))
        self.openei_uri = d.get('uri')
        self.name = d.get('name')
        self.label = d.get('label')
        self.utility = d.get('utility')
        self.description = d.get('description')
        self.source = d.get('source')
        self.source_parent_uri = d.get('sourceparent')
        self.wiring = d.get('phasewiring')
        self.eia_id = d.get('eia')

        # Versioning: each year's re-filing of a rate becomes a new label/GUID in URDB.
        # `supersedes` points at the label of the rate this one replaced, letting you
        # walk a single rate's history back through time.
        self.supersedes = d.get('supercedes') or d.get('supersedes')

        start_d = d.get('startdate')
        self.begin_date = datetime.datetime.utcfromtimestamp(start_d) if start_d else None
        end_d = d.get('enddate')
        self.end_date = datetime.datetime.utcfromtimestamp(end_d) if end_d else None

        # Eligibility requirements: whether a customer's demand/usage/voltage/wiring
        # qualifies them for this rate.
        self.peak_kw_capacity_min = d.get('peakkwcapacitymin')
        self.peak_kw_capacity_max = d.get('peakkwcapacitymax')
        self.demand_units = d.get('demandunits')
        self.peak_kw_capacity_history = d.get('peakkwcapacityhistory')

        self.peak_kwh_usage_min = d.get('peakkwhusagemin')
        self.peak_kwh_usage_max = d.get('peakkwhusagemax')
        self.peak_kwh_usage_history = d.get('peakkwhusagehistory')

        self.voltage_minimum = d.get('voltageminimum')
        self.voltage_maximum = d.get('voltagemaximum')
        self.voltage_category = d.get('voltagecategory')

        # Fixed/minimum charges
        self.fixed_charge_first_meter = d.get('fixedchargefirstmeter')
        self.fixed_charge_ea_addl = d.get('fixedchargeeaaddl')
        self.fixed_charge_units = d.get('fixedchargeunits')
        self.min_charge = d.get('mincharge')
        self.min_charge_units = d.get('minchargeunits')

        # Keep the raw dict around so nothing is lost between what OpenEI returns
        # and what gets persisted (e.g. by a downstream ETL/warehouse layer).
        self.raw = d

        self.rate_schedule = None

    def __str__(self):
        return '<{} : {} : {}>'.format(self.label, self.utility, self.name)

    def __repr__(self):
        return '<Rate("{}", "{}", "{}")>'.format(self.label, self.utility, self.name)

    def is_active(self, dt: datetime.datetime):
        if self.begin_date:
            if self.begin_date <=  dt:
                return dt < self.end_date if self.end_date else True
        return False

    def qualifies(self, demand_kw: float = None, usage_kwh: float = None):
        """Checks whether a customer with the given peak demand and/or usage would
        qualify for this rate, based on its eligibility min/max fields.

        Any bound that is ``None`` or ``0`` in the source data is treated as "no limit",
        matching how OpenEI represents unbounded fields.

        :param  demand_kw:  A customer's peak demand, in kW. Ignored if ``None``.
        :param  usage_kwh:  A customer's peak usage, in kWh. Ignored if ``None``.
        :return:    ``True`` if the supplied values fall within this rate's eligibility bounds.
        """
        if demand_kw is not None:
            if self.peak_kw_capacity_min and demand_kw < self.peak_kw_capacity_min:
                return False
            if self.peak_kw_capacity_max and demand_kw > self.peak_kw_capacity_max:
                return False

        if usage_kwh is not None:
            if self.peak_kwh_usage_min and usage_kwh < self.peak_kwh_usage_min:
                return False
            if self.peak_kwh_usage_max and usage_kwh > self.peak_kwh_usage_max:
                return False

        return True

    def get_rate_schedule(self, api: OpenEIApi):
        params = {
            'getpage': self.label,
            'detail': 'full',
            'limit': 1
        }
        code, items = api.rate_query(params)

        if items:
            self.rate_schedule = RateSchedule(items[0])

            return self.rate_schedule

