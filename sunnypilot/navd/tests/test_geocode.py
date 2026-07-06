"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import json

from openpilot.sunnypilot.navd.geocode import parse_geocode_response, Place
from openpilot.sunnypilot.navd.helpers import Coordinate


def sample_response() -> dict:
  # Two DFW results: an address in Dallas and a POI in Fort Worth.
  return {
    "features": [
      {"place_name": "1500 Marilla St, Dallas, Texas 75201", "text": "1500 Marilla St",
       "center": [-96.7970, 32.7767]},
      {"place_name": "Sundance Square, Fort Worth, Texas 76102", "text": "Sundance Square",
       "center": [-97.3327, 32.7555]},
      {"text": "No center feature"},  # malformed: should be skipped
    ],
  }


class TestGeocodeParse:
  def test_parses_features(self):
    places = parse_geocode_response(sample_response())
    assert len(places) == 2  # malformed one skipped
    assert places[0].name.startswith("1500 Marilla")
    # center is [lng, lat] -> Coordinate(lat, lng)
    assert abs(places[0].coordinate.latitude - 32.7767) < 1e-4
    assert abs(places[0].coordinate.longitude - (-96.7970)) < 1e-4

  def test_empty_response(self):
    assert parse_geocode_response({}) == []
    assert parse_geocode_response({"features": []}) == []

  def test_destination_json_roundtrip(self):
    place = Place("Test Place", Coordinate(32.9, -97.0))
    d = json.loads(place.to_destination_json())
    assert d["latitude"] == 32.9
    assert d["longitude"] == -97.0
    assert d["place_name"] == "Test Place"
