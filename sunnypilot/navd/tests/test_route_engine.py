"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.constants import CV
from openpilot.sunnypilot.navd.helpers import Coordinate
from openpilot.sunnypilot.navd.route_engine import Route


def _banner(text, mtype, modifier, dist):
  return [{
    "distanceAlongGeometry": dist,
    "primary": {"text": text, "type": mtype, "modifier": modifier},
  }]


def sample_route() -> dict:
  """A short east-bound route: two ~55 m steps then arrival, 30 mph limit."""
  return {
    "routes": [{
      "legs": [{
        "annotation": {
          "maxspeed": [
            {"speed": 30, "unit": "mph"},
            {"speed": 30, "unit": "mph"},
          ],
        },
        "steps": [
          {
            "distance": 55.66, "duration": 5.0, "duration_typical": 5.0,
            "geometry": {"coordinates": [[0.0, 0.0], [0.0005, 0.0]]},
            "bannerInstructions": _banner("Main St", "turn", "right", 55.66),
          },
          {
            "distance": 55.66, "duration": 5.0, "duration_typical": 5.0,
            "geometry": {"coordinates": [[0.0005, 0.0], [0.001, 0.0]]},
            "bannerInstructions": _banner("2nd Ave", "turn", "left", 55.66),
          },
          {
            "distance": 0.0, "duration": 0.0, "duration_typical": 0.0,
            "geometry": {"coordinates": [[0.001, 0.0], [0.001, 0.0]]},
            "bannerInstructions": _banner("Destination", "arrive", "straight", 0.0),
          },
        ],
      }],
    }],
  }


class TestRouteParse:
  def test_parses_steps_and_geometry(self):
    route = Route.from_mapbox_json(sample_route())
    assert len(route.steps) == 3
    assert route.step_idx == 0
    assert route.valid
    assert len(route.full_geometry) == 6  # 2 coords per step

  def test_empty_response_is_invalid(self):
    route = Route.from_mapbox_json({"routes": []})
    assert not route.valid

  def test_maxspeed_parsed(self):
    route = Route.from_mapbox_json(sample_route())
    assert abs(route.steps[0].maxspeed_ms - 30 * CV.MPH_TO_MS) < 0.5


class TestProgress:
  def test_initial_instruction(self):
    route = Route.from_mapbox_json(sample_route())
    inst = route.update_progress(Coordinate(0.0, 0.0))
    assert inst.valid
    assert inst.maneuver_primary_text == "Main St"
    assert inst.maneuver_modifier == "right"
    assert abs(inst.maneuver_distance - 55.66) < 2.0
    # remaining ~ two steps
    assert abs(inst.distance_remaining - 111.0) < 3.0
    assert abs(inst.speed_limit - 30 * CV.MPH_TO_MS) < 0.5
    assert not inst.off_route

  def test_advances_past_maneuver(self):
    route = Route.from_mapbox_json(sample_route())
    route.update_progress(Coordinate(0.0, 0.0))
    # drive to just before the first maneuver point (lng ~0.00049)
    inst = route.update_progress(Coordinate(0.0, 0.00049))
    assert route.step_idx == 1
    assert inst.maneuver_primary_text == "2nd Ave"
    assert inst.maneuver_modifier == "left"

  def test_distance_decreases(self):
    route = Route.from_mapbox_json(sample_route())
    d0 = route.update_progress(Coordinate(0.0, 0.0)).distance_remaining
    d1 = route.update_progress(Coordinate(0.0, 0.0003)).distance_remaining
    assert d1 < d0

  def test_off_route_detected(self):
    route = Route.from_mapbox_json(sample_route())
    inst = route.update_progress(Coordinate(1.0, 1.0))  # far from any geometry
    assert inst.off_route
