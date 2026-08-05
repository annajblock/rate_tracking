# Seed list of major California utilities for the initial warehouse test case.
#
# These names need to match the `utility` field exactly as OpenEI/URDB has it on file
# (the `ratesforutility` API parameter does an exact/substring match against this field).
# If a sync comes back empty for one of these, check the utility's rates on
# https://apps.openei.org/USURDB/ for the exact name OpenEI uses before assuming
# something's broken - utility names in URDB aren't always what you'd guess
# (e.g. "Pacific Gas & Electric Co" not "PG&E").
#
# This list intentionally starts small (the big three IOUs plus a couple of major
# municipal utilities) since the initial scope is a California test case before
# expanding to all US commercial/industrial utilities.

CALIFORNIA_UTILITIES = [
    "Pacific Gas & Electric Co",
    "Southern California Edison Co",
    "San Diego Gas & Electric Co",
    "Los Angeles Department of Water & Power",
    "Sacramento Municipal Utility District",
    "Imperial Irrigation District",
    "Turlock Irrigation District",
    "Modesto Irrigation District",
    "Anaheim City of",
    "Riverside City of",
    "Roseville City of",
    "Silicon Valley Power (City of Santa Clara)",
    "Burbank Water & Power",
    "Glendale Water & Power",
    "Pasadena Water & Power",
]

# Sectors relevant to this build (see openei_rates/warehouse/README for scope decisions).
SECTORS = ["Commercial", "Industrial"]
