"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Address / place search (forward geocoding) via the Mapbox Geocoding API.

Biased to the Dallas-Fort Worth metroplex so local results rank first -- typing
"main st" or "sundance square" resolves to the DFW one rather than somewhere across
the country. The parsing is IO-free (`parse_geocode_response`) so it can be tested
without network; `geocode()` wraps it with the HTTP call.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import requests

from openpilot.sunnypilot.navd.helpers import Coordinate

MAPBOX_BASE = os.environ.get("MAPBOX_BASE", "https://api.mapbox.com")

# DFW metroplex bias. Proximity ranks results near this point; bbox restricts to the
# Fort Worth <-> Dallas span incl. the Mid-Cities (Arlington, Irving, HEB, Grand Prairie).
DFW_PROXIMITY = (-96.95, 32.80)          # (lng, lat) ~ between Dallas and Fort Worth
DFW_BBOX = (-97.55, 32.55, -96.45, 33.10)  # (min_lng, min_lat, max_lng, max_lat)


@dataclass
class Place:
  name: str
  coordinate: Coordinate

  def to_destination_json(self) -> str:
    return json.dumps({
      "latitude": self.coordinate.latitude,
      "longitude": self.coordinate.longitude,
      "place_name": self.name,
    })


def parse_geocode_response(resp: dict) -> list[Place]:
  """Turn a Mapbox Geocoding response into Places. IO-free / testable."""
  places: list[Place] = []
  for feat in resp.get("features", []):
    center = feat.get("center")
    if not center or len(center) < 2:
      continue
    name = feat.get("place_name") or feat.get("text") or "Unknown"
    # Mapbox center is [lng, lat]
    places.append(Place(name=name, coordinate=Coordinate(center[1], center[0])))
  return places


def geocode(query: str, token: str, *, limit: int = 5,
            proximity: tuple[float, float] = DFW_PROXIMITY,
            bbox: tuple[float, float, float, float] = DFW_BBOX) -> list[Place]:
  """Forward-geocode `query` to a ranked list of DFW-biased Places."""
  query = query.strip()
  if not query or not token:
    return []

  url = f"{MAPBOX_BASE}/geocoding/v5/mapbox.places/{requests.utils.quote(query)}.json"
  params = {
    "access_token": token,
    "limit": limit,
    "country": "US",
    "language": "en",
    "types": "address,poi,place,neighborhood,locality",
    "proximity": f"{proximity[0]},{proximity[1]}",
    "bbox": ",".join(str(b) for b in bbox),
  }
  try:
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return parse_geocode_response(resp.json())
  except requests.exceptions.RequestException:
    return []
  except (ValueError, KeyError):
    return []
