import numpy as np
import numba as nb
from ..data_objects import Period, TierIndex, Tier


@nb.njit
def get_tou_info(month: int, hour: int, schedule: np.array, struct: np.array):
    """Returns the rate structure array (a set of tiers) for the supplied month/hour.

    :param month: Integer in the range [1,12] representing the month.
    :param hour: Integer in the range [0,23] representing the hour of the day.
    :param schedule: A Numpy array containing the time of use period indexes.
                      May be 1-D (months only) or 2-D (months x hours).
    :param struct: A Numpy array containing the associated rate information.
    :returns: A Numpy array of the rate information for the current month and hour.
            Returns *None* if either **struct** or **schedule** are *None*.
    :raises IndexError: If either **month** or **hour** are out of range.
    """
    if month < 1 or month > 12 or hour < 0 or hour > 23:
        raise IndexError('Supplied month or hour is out of range')

    if schedule is None or struct is None:
        return None

    month_index = month - 1

    if schedule.ndim == 2:
        return struct[schedule[month_index, hour]]
    elif schedule.ndim == 1:
        return struct[schedule[month_index]]
    else:
        raise ValueError(
            'Schedule has wrong number of dimensions. Must be of dimension 1 or 2.'
        )


@nb.njit
def get_flat_month(month: int, schedule: np.array, struct: np.array):
    """Returns the rate structure array (a set of tiers) for a flat (monthly) demand charge.

    :param month: Integer in the range [1,12] representing the month.
    :param schedule: A Numpy array containing the time of use period indexes.
    :param struct: A Numpy array containing the associated rate information.
    :returns: A Numpy array of the rate information for the current month.
            Returns *None* if either **struct** or **schedule** are *None*.
    :raises IndexError: If **month** is out of range.
    """
    if month < 1 or month > 12:
        raise IndexError('Supplied month is out of range')

    if schedule is None or struct is None:
        return None

    return struct[schedule[month - 1]]


@nb.jit(nopython=True, nogil=False)
def get_Tier(qty: float, period: Period,
             struct: np.array, schedule: np.array):
    """Returns the rate information for the supplied TOU.

    :param interval:    A generated Interval

    :type  interval:    `Interval`

    :param schedule:    A Numpy array containing the time of use period
                        indexes.

    :type  schedule:    ``numpy.array`` of type ``float32``

    :param struct:      A Numpy array containing the associated rate
                        information.

    :returns:           A Numpy array of the are information for the current
                        month and hour. The array will have a minimum length
                        of 1. Returns *None* if either **struct** or
                        **schedule** are *None*.

    :raises IndexError: If either **month** or **hour** are out of range.
    """
    if period.hour < 0 or period.hour > 23:
        raise IndexError('Supplied hour is out of range')

    if period.month < 1 or period.month > 12:
        raise IndexError('Supplied month is out of range')

    tou = None
    month_index = period.month - 1
    hour_index = period.hour

    # We have a 2-D array with months and hours
    if schedule.ndim == 2:
        tou = struct[schedule[month_index, hour_index]]

    # We have a struct of just months
    elif schedule.ndim == 1:
        tou = struct[schedule[month_index]]

    else:
        raise ValueError(
            'Struct has wrong number of dimensions. \
            Must be of dimension 1 or 2.'
        )

    # We need a non-none tou
    if tou is None:
        raise ValueError('Supplied schedule array was None')
    
    assert tou.ndim == 2,\
        'Incorrectly formed schedule array. Must be of dimension 3.'
    assert tou.shape[1] == TierIndex.ARRAY_LENGTH,\
        'Incorrectly shaped Tier rows.'

    i = 0
    row = np.array([np.inf, 0., 0., 0.], dtype=np.float32)

    while i < tou.shape[0]:
        row = tou[i, :]
        if row[TierIndex.MAX] <= 0.:
            break
        elif qty <= row[TierIndex.MAX]:        
            break
        i += 1

    return Tier(
        max=row[TierIndex.MAX],
        price=row[TierIndex.RATE],
        adj=row[TierIndex.ADJ],
        sell=row[TierIndex.SELL]
    )
