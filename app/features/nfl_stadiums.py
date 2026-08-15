"""Public NFL stadium coordinates for travel distance. No API."""

from __future__ import annotations

import math

# City/stadium lat, lon — public atlas values, franchise aliases included.
STADIUMS: dict[str, tuple[float, float]] = {
    "ARI": (33.5276, -112.2626),
    "ATL": (33.7550, -84.4010),
    "BAL": (39.2780, -76.6227),
    "BUF": (42.7738, -78.7870),
    "CAR": (35.2258, -80.8528),
    "CHI": (41.8623, -87.6167),
    "CIN": (39.0954, -84.5160),
    "CLE": (41.5061, -81.6995),
    "DAL": (32.7473, -97.0945),
    "DEN": (39.7439, -105.0201),
    "DET": (42.3400, -83.0456),
    "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107),
    "IND": (39.7601, -86.1639),
    "JAX": (30.3239, -81.6373),
    "KC": (39.0489, -94.4839),
    "LAC": (33.9535, -118.3390),
    "LAR": (33.9535, -118.3390),
    "LV": (36.0908, -115.1836),
    "MIA": (25.9580, -80.2389),
    "MIN": (44.9738, -93.2577),
    "NE": (42.0909, -71.2643),
    "NO": (29.9511, -90.0812),
    "NYG": (40.8136, -74.0744),
    "NYJ": (40.8136, -74.0744),
    "PHI": (39.9008, -75.1675),
    "PIT": (40.4468, -80.0158),
    "SEA": (47.5952, -122.3316),
    "SF": (37.4033, -121.9694),
    "TB": (27.9759, -82.5033),
    "TEN": (36.1665, -86.7713),
    "WSH": (38.9076, -76.8645),
    "OAK": (36.0908, -115.1836),
    "WAS": (38.9076, -76.8645),
    "LA": (33.9535, -118.3390),
}


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def travel_miles(away_abbr: str, home_abbr: str, *, neutral: bool = False) -> float:
    if neutral:
        return 0.0
    away = STADIUMS.get(str(away_abbr or "").upper())
    home = STADIUMS.get(str(home_abbr or "").upper())
    if not away or not home:
        return 0.0
    return round(haversine_miles(away, home), 1)
