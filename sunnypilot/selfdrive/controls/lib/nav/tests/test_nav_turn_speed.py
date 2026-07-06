"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.sunnypilot.selfdrive.controls.lib.nav.nav_turn_speed import (
  maneuver_target_speed,
  allowed_speed,
  NO_CONSTRAINT,
  COMFORT_DECEL,
  _TURN,
  _SLIGHT,
  _UTURN,
  _RAMP,
  _ROUNDABOUT,
)


class TestManeuverTargetSpeed:
  def test_plain_turn(self):
    assert maneuver_target_speed("turn", "left") == _TURN
    assert maneuver_target_speed("turn", "right") == _TURN

  def test_slight_turn(self):
    assert maneuver_target_speed("turn", "slight right") == _SLIGHT

  def test_sharp_turn(self):
    # sharp overrides the plain-turn speed
    assert maneuver_target_speed("turn", "sharp left") < _TURN

  def test_uturn(self):
    assert maneuver_target_speed("turn", "uturn") == _UTURN

  def test_off_ramp(self):
    assert maneuver_target_speed("off ramp", "slight right") == _RAMP

  def test_roundabout(self):
    assert maneuver_target_speed("roundabout", "right") == _ROUNDABOUT

  def test_straight_no_constraint(self):
    assert maneuver_target_speed("continue", "straight") == NO_CONSTRAINT
    assert maneuver_target_speed("merge", "straight") == NO_CONSTRAINT
    assert maneuver_target_speed("new name", "straight") == NO_CONSTRAINT

  def test_depart_arrive_no_constraint(self):
    assert maneuver_target_speed("depart", "") == NO_CONSTRAINT
    assert maneuver_target_speed("arrive", "") == NO_CONSTRAINT


class TestAllowedSpeed:
  def test_at_maneuver_returns_target(self):
    # zero distance -> must already be at target speed
    assert abs(allowed_speed(9.0, 0.0) - 9.0) < 1e-6

  def test_far_away_allows_high_speed(self):
    # 300 m out from a 9 m/s turn -> plenty of room, allowed speed is high
    v = allowed_speed(_TURN, 300.0)
    assert v > 25.0  # ~56 mph+, won't constrain highway cruise

  def test_monotonic_in_distance(self):
    near = allowed_speed(_TURN, 30.0)
    far = allowed_speed(_TURN, 120.0)
    assert far > near
    assert near >= _TURN  # never below the target itself

  def test_kinematics_match_formula(self):
    import math
    target, d = 9.0, 50.0
    d_eff = d - target * 1.0
    expected = math.sqrt(target ** 2 + 2 * COMFORT_DECEL * d_eff)
    assert abs(allowed_speed(target, d) - expected) < 1e-6
