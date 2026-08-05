import numba as nb
import numpy as np
import pandas as pd

from typing import Callable

from .sched import get_Tier, get_tou_info, get_flat_month
from ..data_objects import Tier, TierIndex, Period
from .demand import peak_period, basic_period
from .window import (
    window,
    month_changed,
    hour_changed,
    assign_distribute,
    assign_end,
    assign_front,
    assign_at_index
)


@nb.njit
def energy_cost(
        qty_array: np.array,
        price_struct: np.array,
        months: np.array,
        hours: np.array,
        is_weekend: np.array,
        wd_schedule: np.array,
        we_schedule: np.array,
        interval_time: float,
        assignment_func=assign_distribute,
        net_meter: bool = False,
        retail_net: bool = False):
    
    qlen = qty_array.shape[0]

    out = np.zeros(qlen, dtype=np.float32)

    _window(
        qty_array,
        out,
        price_struct,
        months,
        hours,
        is_weekend,
        wd_schedule,
        we_schedule,
        interval_time,
        net_meter,
        hour_changed,
        basic_period,
        assignment_func,
        np.sum
    )

    return out


@nb.njit
def tou_demand_cost(
        qty_array: np.array,
        price_struct: np.array,
        months: np.array,
        hours: np.array,
        is_weekend: np.array,
        window_span: int,
        wd_schedule: np.array,
        we_schedule: np.array,
        interval_hours: float,
        net_meter: bool,
        normalize: bool = False):

    qlen = qty_array.shape[0]

    out = np.zeros(qlen, dtype=np.float32)

    assign_func = assign_distribute
    if normalize:
        assign_func = assign_at_index

    window(
        qty_array,
        out,
        price_struct,
        months,
        hours,
        is_weekend,
        wd_schedule,
        we_schedule,
        interval_hours,
        net_meter,
        month_changed,
        peak_period,
        assignment_func,
        np.sum,
        window_span=window_span
    )

    return out 


@nb.njit
def flat_demand_cost(
        qty_array: np.array,
        price_struct: np.array,
        month_interval: pd.Interval,
        schedule: np.array

        ):

    qlen = qty_array.shape[0]
    out = np.zeros(qlen, dtype=np.float32)
    window(
        qty_array,
        out,
        price_struct,
        month_interval,
        schedule,
        assignment_func,
        np.sum,
    )
    return out

@nb.njit
def get_tou_tier(qty: float, tou: np.array):
    """Given a 2-D array of tier rows for a single time-of-use period, finds the tier that ``qty`` falls into.

    Mirrors the tier-scanning logic in ``sched.get_Tier``: walks the tiers in order and stops at
    the first tier whose max is uncapped (<= 0) or whose max is >= ``qty``.
    """
    if tou is None:
        raise ValueError('Supplied schedule array was None')

    assert tou.ndim == 2, 'Incorrectly formed schedule array. Must be of dimension 2.'
    assert tou.shape[1] == TierIndex.ARRAY_LENGTH, 'Incorrectly shaped Tier rows.'

    i = 0
    row = tou[0, :]
    while i < tou.shape[0]:
        row = tou[i, :]
        if row[TierIndex.MAX] <= 0.:
            break
        elif qty <= row[TierIndex.MAX]:
            break
        i += 1

    return row


@nb.njit
def calculate_tou_cost(qty, month, hour, schedule: np.array, struct: np.array):
    """Calculate the cost of the energy for the interval.
    """
    tou = get_tou_info(month, hour, schedule, struct)

    tier = get_tou_tier(qty, tou)

    rate_price = 0.0
    adj_price = 0.0

    if tier is not None:

        # If we're positive, we use the rate
        if qty >= 0:
            rate_price = qty * tier[TierIndex.RATE]

        # If we're negative, we use the sell price. This is what will happen under NEM 2.0 in Califoirnia.
        else:
            rate_price = qty * tier[TierIndex.SELL]
        
        adj_price = abs(qty) * tier[TierIndex.ADJ]
    
    return adj_price + rate_price

@nb.jit(nopython=True, nogil=False)
def calculate_flat_cost(
    qty: float,
    month: int,
    flat_schedule: np.array,
    flat_struct: np.array,
    ):
    """Calculates the demand charges for a particular quantity of power at a given date and time. 
    """
    flat_price = 0.0
    if flat_schedule is not None and flat_struct is not None:
        # It's a little differnt for flat schedules
        tou = get_flat_month(month, flat_schedule, flat_struct)
        tier = get_tou_tier(qty, tou)
        p = 0.0
        # If we're positive, we use the rate
        if qty >= 0:
            p = qty * tier[TierIndex.RATE]

        # If we're negative, we use the sell price. This is what will happen under NEM 2.0 in Califoirnia.
        else:
            p = qty * tier[TierIndex.SELL]
        
        adj = abs(qty) * tier[TierIndex.ADJ] 

        flat_price = adj + p
    
    return flat_price 





    

