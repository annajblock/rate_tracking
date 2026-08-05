
import numpy as np
import numba as nb

from ..data_objects import Period, DemandResult


@nb.njit
def get_interval_max_demand(a: np.array, n_intervals: int = 3, floor: float = 0, ceiling: float = 0):
    """Finds the highest interval average that will be used for a demand charge subject to a floor and ceiling.

    When determining n_intervals, it is the responsibility of the calling function or method to supply an appropriate value.
    This function is agnostic about the length of time an interval represents.

    :param a: An array demand intervals
    :param n_intervals: (Optional) The number of intervals to average for the demand charge. Defaults to 3.
    :param floor: (Optional) A minimum quantity demanded for a demand charge. Defaults to 0.
    :param ceiling: (Optional) A maximum quantity demanded. Defaults to 0 (no ceiling).
    :returns: A 3-item tuple with the format (index, reported_maximum, actual_maximum),
            where reported_maximum is constrained by the floor and ceiling, while the actual_maximum is the maximum found.
    :raises: ValueError if n_intervals is <= 0 or the array is empty; IndexError if n_intervals > len(a).
    """
    if n_intervals <= 0 or a.shape[0] == 0:
        raise ValueError
    length = a.shape[0]
    if n_intervals > length:
        raise IndexError
    dmax = 0.0
    idx = 0
    for i in range(length - n_intervals + 1):
        x = a[i:i + n_intervals].mean()
        if x > dmax:
            dmax = x
            idx = i
    reported = dmax
    if floor > 0:
        reported = max(floor, reported)
    if ceiling > 0:
        reported = min(ceiling, reported)
    return (idx, reported, dmax)


@nb.njit
def peak_period(
        qty_array: np.array,
        period: Period,
        net: bool,
        agg_func: callable,
        ):
    length = qty_array.shape[0]

    if period.span > length:
        raise IndexError('qty_array is smaller than the interval window.')
    if agg_func is None:
        raise ValueError('Cannot have a NoneType callable.')

    peak = 0
    idx = 0
    for i in range(length - period.span):
        x = agg_func(qty_array[i:i + period.span])
        if x > peak:
            peak = x
            idx = i
   
    normalized = False
    if agg_func == np.mean:
        normalized = True

    return DemandResult(
        normalized=normalized,
        index=idx,
        qty=peak,
        span=period.span,
        net_metered=net
    )


@nb.njit
def basic_period(
        qty_array: np.array,
        period: Period,
        net: bool,
        agg_func: callable):

    if agg_func is None:
        raise ValueError('Cannot have a NoneType callable.')

    result = 0.0

    if net:
        result = agg_func(qty_array)
    else:
        result = agg_func(np.maximum(qty_array, 0.))

    normalized = False

    if agg_func == np.mean:
        normalized = True

    return DemandResult(
        normalized=normalized,
        index=0,
        qty=result,
        span=period.span,
        net_metered=net
    )
