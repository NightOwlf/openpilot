"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
from openpilot.common.constants import CV
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_positioning.auto_lane_positioning import (
  AutoLanePositioningController,
  ALPInput,
  qualify,
)
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_positioning.constants import (
  ALPConstants as C,
  ALPDirection,
  ALPMode,
  ALPReason,
)


def held_up_input(**overrides) -> ALPInput:
  """A baseline snapshot where a left pass is clearly warranted."""
  inp = ALPInput(
    lateral_active=True,
    v_ego=65 * CV.MPH_TO_MS,
    v_cruise_setpoint=70 * CV.MPH_TO_MS,
    lead_status=True,
    lead_d_rel=45.0,
    lead_v_lead=55 * CV.MPH_TO_MS,   # ~15 mph slower than set speed
    lead_prob=0.9,
    left_blindspot=False,
    right_blindspot=False,
    lane_change_active=False,
    brake_pressed=False,
    steering_pressed=False,
    pass_on_left=True,
  )
  for k, v in overrides.items():
    setattr(inp, k, v)
  return inp


class TestQualify:
  def test_baseline_qualifies_left(self):
    ok, direction, reason = qualify(held_up_input())
    assert ok
    assert direction == ALPDirection.LEFT
    assert reason == ALPReason.SLOW_LEAD

  def test_low_speed_suppresses(self):
    ok, _, reason = qualify(held_up_input(v_ego=25 * CV.MPH_TO_MS))
    assert not ok
    assert reason == ALPReason.LOW_SPEED

  def test_no_lead_suppresses(self):
    ok, _, reason = qualify(held_up_input(lead_status=False))
    assert not ok
    assert reason == ALPReason.NO_LEAD

  def test_low_lead_prob_suppresses(self):
    ok, _, reason = qualify(held_up_input(lead_prob=0.2))
    assert not ok
    assert reason == ALPReason.NO_LEAD

  def test_lead_not_slow_suppresses(self):
    # Lead essentially at set speed -> not holding us up.
    ok, _, reason = qualify(held_up_input(lead_v_lead=69 * CV.MPH_TO_MS))
    assert not ok
    assert reason == ALPReason.LEAD_NOT_SLOW

  def test_lead_too_far_suppresses(self):
    ok, _, reason = qualify(held_up_input(lead_d_rel=250.0))
    assert not ok
    assert reason == ALPReason.LEAD_TOO_FAR

  def test_lead_too_close_suppresses(self):
    ok, _, reason = qualify(held_up_input(lead_d_rel=5.0))
    assert not ok
    assert reason == ALPReason.LEAD_TOO_FAR

  def test_blindspot_on_pass_side_blocks(self):
    ok, _, reason = qualify(held_up_input(left_blindspot=True))
    assert not ok
    assert reason == ALPReason.SIDE_BLOCKED

  def test_blindspot_on_other_side_ignored(self):
    ok, direction, _ = qualify(held_up_input(right_blindspot=True))
    assert ok
    assert direction == ALPDirection.LEFT

  def test_driver_override_blocks(self):
    assert not qualify(held_up_input(brake_pressed=True))[0]
    assert not qualify(held_up_input(steering_pressed=True))[0]

  def test_already_changing_blocks(self):
    ok, _, reason = qualify(held_up_input(lane_change_active=True))
    assert not ok
    assert reason == ALPReason.ALREADY_CHANGING

  def test_pass_on_right_when_configured(self):
    ok, direction, _ = qualify(held_up_input(pass_on_left=False))
    assert ok
    assert direction == ALPDirection.RIGHT

  def test_unknown_set_speed_falls_back_to_ego(self):
    # No set speed known; lead clearly slower than ego -> still qualifies.
    ok, _, _ = qualify(held_up_input(v_cruise_setpoint=0.0, lead_v_lead=50 * CV.MPH_TO_MS))
    assert ok


class TestController:
  def _make(self, mode=ALPMode.ASSIST) -> AutoLanePositioningController:
    ctrl = AutoLanePositioningController()
    ctrl.mode = mode
    return ctrl

  def _run(self, ctrl, inp, seconds):
    steps = int(round(seconds / DT_MDL))
    dec = None
    for _ in range(steps):
      dec = ctrl.evaluate(inp)
    return dec

  def test_off_mode_never_activates(self):
    ctrl = self._make(ALPMode.OFF)
    dec = self._run(ctrl, held_up_input(), 5.0)
    assert not dec.active
    assert dec.reason == ALPReason.DISABLED

  def test_requires_debounce_before_activation(self):
    ctrl = self._make()
    # Just under the on-time: should still be inactive.
    dec = self._run(ctrl, held_up_input(), C.SUGGEST_ON_TIME - 0.2)
    assert not dec.active
    assert dec.candidate

  def test_activates_after_debounce(self):
    ctrl = self._make()
    dec = self._run(ctrl, held_up_input(), C.SUGGEST_ON_TIME + 0.2)
    assert dec.active
    assert dec.direction == ALPDirection.LEFT
    assert dec.reason == ALPReason.SLOW_LEAD

  def test_cooldown_after_suggestion_clears(self):
    ctrl = self._make()
    self._run(ctrl, held_up_input(), C.SUGGEST_ON_TIME + 0.2)
    assert ctrl.suggestion_active
    # Lead clears -> suggestion falls, cooldown starts.
    self._run(ctrl, held_up_input(lead_status=False), C.SUGGEST_HOLD_TIME + 0.2)
    assert ctrl.cooldown_timer > 0.0
    # New held-up lead during cooldown must not immediately re-activate.
    dec = ctrl.evaluate(held_up_input())
    assert not dec.active
    assert dec.reason == ALPReason.COOLDOWN

  def test_lateral_inactive_resets(self):
    ctrl = self._make()
    self._run(ctrl, held_up_input(), C.SUGGEST_ON_TIME + 0.2)
    dec = ctrl.evaluate(held_up_input(lateral_active=False))
    assert not dec.active
    assert dec.reason == ALPReason.DISABLED
    assert ctrl.on_timer == 0.0

  def test_notify_lane_change_starts_cooldown(self):
    ctrl = self._make()
    ctrl.notify_lane_change_started()
    assert ctrl.cooldown_timer > 0.0
