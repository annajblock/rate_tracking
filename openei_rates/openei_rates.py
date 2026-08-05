# -*- coding: utf-8 -*-

from .api import OpenEIApi
from .rate import Rate
import datetime
import re
from urllib import parse
from . import logger

class OpenEIRates(object):

    allowed_sectors = ['Residential', 'Commercial', 'Industrial', 'Lighting']

    def __init__(self, api_key):

        self.api = OpenEIApi(api_key)

        self.rates = []

        self.utility_filter = ''
        self.rate_name_filter = ''
        self.active_date = datetime.datetime.now()
    

    def _sectors(self, sector_str: str):
        possible_sectors = list(map(lambda x: x.title(), re.findall(r'[\w]+', sector_str)))
        return [s for s in possible_sectors if s in self.allowed_sectors]

    
    def filter_rates(
            self,
            rates: list = [],
            utility: str = '',
            name: str = '',
            active: bool = True,
            only_approved: bool = False,
            sector: str = '',
            replace = False,
            active_date: datetime.datetime = None
        ):


        if not active_date:
            active_date = self.active_date

        newlist = []
        rlist = rates if rates else self.rates
        
        sectors = self._sectors(sector)

        rate: Rate
        for rate in rlist:
            add_status = []

            # If utility is blank, default to True
            # Otherwise, see if the string is in the field
            if utility:
                add_status.append(utility in rate.utility)

            # Same as utility, but with name
            if name:
                add_status.append(name in rate.name)

            # Get approval status equality
            if only_approved:
                add_status.append(rate.approved)

            # See if the rate's sector is in of the allowed sectors of the filter
            if sectors:
                add_status.append(rate.sector in sectors)

            if active and active_date:
                add_status.append( rate.is_active(active_date) )

            if all(add_status):
                newlist.append(rate)
        
        if replace or not rates:
            self.rates = newlist
        
        return newlist         

    def get_rates_geocoded(
        self,
        address: str,
        active: bool = True,
        sector: str = '',
        active_date: datetime.datetime = datetime.datetime.now(),
        replace = True,
        ):
        """Looks up rates based on a a geocoded address.
        This uses the [Google geocoding API](https://developers.google.com/maps/documentation/geocoding/) in OpenEi's backend.

        :param  address: A location to look for rates.
        :type   address: ``str``
        """
        params = {
            'address': address.title(),        }
        if sector and sector.title() in ['Residential', 'Lighting', 'Commercial', 'Industrial']:
            params['sector'] = sector.title()

        code, items = self.api.rate_query(params)

        if code == 200 and items:
            rates = []
            for item in items:
                rates.append(Rate(item))

            return self.filter_rates(
                rates,
                active = active,
                sector = sector,
                replace = replace
            )

        return []

    def get_rates_for_utility(
            self,
            utility: str,
            sector: str = '',
            detail: str = 'full',
            page_size: int = 500,
            max_pages: int = 20,
            append: bool = False,
        ):
        """Fetches a utility's full rate history (every label OpenEI has ever issued for it,
        i.e. every year's re-filing) using the ``ratesforutility`` API parameter, paginating
        through results until fewer than ``page_size`` records come back.

        This does not filter by date. Callers wanting only e.g. the last 10 years should
        filter the returned ``Rate`` objects on ``begin_date``/``end_date`` afterwards - OpenEI
        doesn't support pre-filtering to a *span* of dates server-side, only a single
        ``effective_on_date``.

        :param  utility:    The exact utility name as OpenEI knows it (see the ``utility``
                             field on existing ``Rate`` objects, or the utility_companies endpoint).
        :type   utility:    ``str``

        :param  sector:     One of 'Residential', 'Commercial', 'Industrial', 'Lighting'.
                             Leave blank to fetch all sectors.
        :type   sector:     ``str``

        :param  detail:     'full' (get every field, needed to build a RateSchedule/store charges)
                             or 'minimal'. Defaults to 'full'.
        :type   detail:     ``str``

        :param  page_size:  How many records to request per page. OpenEI caps this at 500.
        :type   page_size:  ``int``

        :param  max_pages:  A safety limit on how many pages to fetch, in case pagination
                             doesn't terminate as expected. Defaults to 20 (up to 10,000 records).
        :type   max_pages:  ``int``

        :param  append:     If ``True``, add the fetched rates to ``self.rates`` instead of
                             just returning them.
        :type   append:     ``bool``

        :return:    A ``list`` of ``Rate`` objects.
        """
        params = {
            'ratesforutility': utility,
            'detail': detail,
            'limit': min(page_size, 500),
        }
        if sector and sector.title() in self.allowed_sectors:
            params['sector'] = sector.title()

        rates = []
        offset = 0
        for _ in range(max_pages):
            page_params = dict(params, offset=offset)
            code, items = self.api.rate_query(page_params)

            if code != 200 or not items:
                break

            rates.extend(Rate(item) for item in items)

            if len(items) < params['limit']:
                # Short page - we've reached the end.
                break

            offset += params['limit']

        if append:
            self.rates.extend(rates)

        return rates

    def get_rate_by_label(self, label: str, replace = False, append=False, use_cached=False):
        """Looks up rates based onthe rate's label.

        :param  label: An OpenEI rate label
        :type   label: ``str``

        :param  replace:    Determines if the found rate should replace the current list of rates. Defaults to ``False``.
        :type   replace: ``bool``

        :param  append:     Determines if the rate should be appended to the current rate list. Defaults to ``False``.
        :type   append:     ``bool``

        :param  use_cached: If set to ``True``, looks in current list of rates before attempting to fetch a new one.
                            Defaults to ``False``.
        :type   use_cached: ``bool``

        :return:    A `Rate` if found, ``None`` if not found.
        """

        if use_cached:
            for rate in self.rates:
                if rate.label == label:
                    return Rate

        params = {
            'getpage': label,
            'detail': 'full',
        }
        code, items = self.api.rate_query(params)

        if code == 200 and items:
            rate = Rate(items[0])
            if rate:
                if replace:
                    self.rates = [rate]
                elif append:
                    self.rates.append(rate)
                return rate
        return None


    def get_rate_by_url(self, url: str, replace = False, append=False):
        """Looks up rates based onthe URL for the rate.
        [OpenEI provides an online search tool](https://openei.org/apps/USURDB/) to locate rate information. 
        You can simply paste a rate's url into this function, and it should be found.

        :param  url: A valid OpenEI rate URL
        :type   url: ``str``

        :param  replace:    Determines if the found rate should replace the current list of rates. Defaults to ``False``.
        :type   replace: ``bool``

        :param  append:     Determines if the rate should be appended to the current rate list. Defaults to ``False``.
        :type   append:     ``bool``

        :return:    A `Rate` if found, ``None`` if not found.
        """
        try:
            parsed = parse.urlparse(url)
            label = parsed.path.split('/')[-1]
            rate = self.get_rate_by_label(label, replace=replace, append=append, use_cached=False)
            if not rate:
                logger.warning('The URL specified wa snot a valid OpenEI rate URL.')
            return rate
        except:
            logger.warning('A malformed URL was provided')
        return None






