"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Auto Lane Positioning (ALP) -- the "automatic passing" decision maker.

The decision core (`evaluate`) is intentionally free of cereal/numpy so it can be
unit tested and replayed anywhere. `AutoLanePositioningController` wraps it with
Params reads and timing state, and is what `DesireHelper` instantiates.

MVP scope: ASSIST mode only. The controller *detects* when a slower lead is
holding the ego up and an adjacent lane looks clear, and surfaces a suggestion
(direction + reason). It does NOT command a lane change here -- execution stays
with the driver (turn stalk -> existing nudgeless AutoLaneChange path). AUTO mode
is stubbed as an explicit, guarded future hook and is inert for now.
"""

from dataclasses import dataclass, field

from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_positioning.constants import (
  ALPConstants as C,
  ALPDirection,
  ALPMode,
  ALPReason,
)

# PARAMS_UPDATE_PERIOD is in seconds; convert to a model-frame count for the read cadence.
_PARAM_READ_FRAMES = max(1, int(round(PARAMS_UPDATE_PERIOD / DT_MDL)))


def _interp(x: float, xp: list[float], fp: list[float]) -> float:
  """Minimal clamped linear interpolation (numpy-free, so the core is portable)."""
  if x <= xp[0]:
    return fp[0]
  if x >= xp[-1]:
    return fp[-1]
  for i in range(1, len(xp)):
    if x < xp[i]:
      x0, x1 = xp[i - 1], xp[i]
      y0, y1 = fp[i - 1], fp[i]
      return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
  return fp[-1]


@dataclass
class ALPInput:
  """A single decision-cycle snapshot, in plain units (SI, m/s, m)."""
  lateral_active: bool = False
  v_ego: float = 0.0
  v_cruise_setpoint: float = 0.0   # target/set speed; <= 0 means unknown
  lead_status: bool = False
  lead_d_rel: float = 0.0
  lead_v_lead: float = 0.0
  lead_prob: float = 0.0
  left_blindspot: bool = False
  right_blindspot: bool = False
  lane_change_active: bool = False  # a lane change already in progress
  brake_pressed: bool = False
  steering_pressed: bool = False
  pass_on_left: bool = C.DEFAULT_PASS_LEFT


@dataclass
class ALPDecision:
  """Result of one cycle. `active` gates UI/execution; the rest is for logs/replay."""
  active: bool = False
  direction: int = ALPDirection.NONE
  reason: int = ALPReason.NONE
  candidate: bool = False          # instantaneous qualification (pre-debounce)
  on_timer: float = 0.0            # how long the candidate has qualified
  cooldown_remaining: float = 0.0


def _preferred_direction(pass_on_left: bool) -> int:
  return ALPDirection.LEFT if pass_on_left else ALPDirection.RIGHT


def _side_blocked(direction: int, left_blindspot: bool, right_blindspot: bool) -> bool:
  if direction == ALPDirection.LEFT:
    return left_blindspot
  if direction == ALPDirection.RIGHT:
    return right_blindspot
  return True


def qualify(inp: ALPInput) -> tuple[bool, int, int]:
  """Instantaneous, memoryless check. Returns (candidate, direction, reason).

  A candidate means: right now, a pass would make sense. Debounce/cooldown are
  applied by the controller on top of this.
  """
  direction = _preferred_direction(inp.pass_on_left)

  # Driver is actively in control of speed/steering -> never suggest.
  if inp.brake_pressed or inp.steering_pressed:
    return False, ALPDirection.NONE, ALPReason.DRIVER_OVERRIDE

  # Don't stack maneuvers.
  if inp.lane_change_active:
    return False, ALPDirection.NONE, ALPReason.ALREADY_CHANGING

  # Highway only.
  if inp.v_ego < C.MIN_ENGAGE_SPEED:
    return False, ALPDirection.NONE, ALPReason.LOW_SPEED

  # Need a stable lead to consider passing.
  if not inp.lead_status or inp.lead_prob < C.MIN_LEAD_PROB:
    return False, ALPDirection.NONE, ALPReason.NO_LEAD

  # The lead must actually be holding us up: slower than our set speed by a margin.
  # If we don't know the set speed, fall back to "slower than current ego speed".
  target = inp.v_cruise_setpoint if inp.v_cruise_setpoint > 0 else inp.v_ego
  if inp.lead_v_lead > target - C.SLOWER_THAN_SET_MARGIN:
    return False, ALPDirection.NONE, ALPReason.LEAD_NOT_SLOW

  # Lead must be within the speed-scaled engagement band.
  d_min = _interp(inp.v_ego, C.ENGAGE_DIST_BP, C.ENGAGE_DIST_MIN_V)
  d_max = _interp(inp.v_ego, C.ENGAGE_DIST_BP, C.ENGAGE_DIST_MAX_V)
  if inp.lead_d_rel < d_min or inp.lead_d_rel > d_max:
    return False, ALPDirection.NONE, ALPReason.LEAD_TOO_FAR

  # Chosen passing lane must be clear.
  if _side_blocked(direction, inp.left_blindspot, inp.right_blindspot):
    return False, ALPDirection.NONE, ALPReason.SIDE_BLOCKED

  return True, direction, ALPReason.SLOW_LEAD


class AutoLanePositioningController:
  """Stateful wrapper: debounces qualification, enforces cooldown, reads Params."""

  def __init__(self, desire_helper=None):
    self.DH = desire_helper
    self.params = Params()

    self.mode = ALPMode.OFF
    self.param_read_counter = 0

    self.on_timer = 0.0
    self.hold_timer = 0.0
    self.cooldown_timer = 0.0
    self.suggestion_active = False

    self.decision = ALPDecision()
    self.read_params()

  # --- Params ---
  def read_params(self) -> None:
    self.mode = self.params.get("AutoLanePositioning", return_default=True) or ALPMode.OFF

  def update_params(self) -> None:
    if self.param_read_counter % _PARAM_READ_FRAMES == 0:
      self.read_params()
    self.param_read_counter += 1

  def reset(self) -> None:
    self.on_timer = 0.0
    self.hold_timer = 0.0
    self.suggestion_active = False

  # --- Core update ---
  def evaluate(self, inp: ALPInput) -> ALPDecision:
    """Advance one DT_MDL cycle and return the current decision."""
    if self.cooldown_timer > 0.0:
      self.cooldown_timer = max(0.0, self.cooldown_timer - DT_MDL)

    if self.mode == ALPMode.OFF or not inp.lateral_active:
      self.reset()
      self.decision = ALPDecision(reason=ALPReason.DISABLED, cooldown_remaining=self.cooldown_timer)
      return self.decision

    candidate, direction, reason = qualify(inp)

    # In cooldown we still report *why* but suppress activation.
    if self.cooldown_timer > 0.0:
      self.on_timer = 0.0
      self.hold_timer = 0.0
      self.suggestion_active = False
      self.decision = ALPDecision(active=False, direction=ALPDirection.NONE,
                                  reason=ALPReason.COOLDOWN, candidate=candidate,
                                  on_timer=0.0, cooldown_remaining=self.cooldown_timer)
      return self.decision

    if candidate:
      self.on_timer += DT_MDL
      self.hold_timer = C.SUGGEST_HOLD_TIME
    else:
      self.on_timer = 0.0
      self.hold_timer = max(0.0, self.hold_timer - DT_MDL)

    was_active = self.suggestion_active
    # Turn on once the candidate has persisted; hold briefly through brief dropouts.
    if self.on_timer >= C.SUGGEST_ON_TIME:
      self.suggestion_active = True
    elif self.hold_timer <= 0.0:
      self.suggestion_active = False

    # On the falling edge of an active suggestion, start the cooldown.
    if was_active and not self.suggestion_active:
      self.cooldown_timer = C.COOLDOWN_TIME

    out_dir = direction if self.suggestion_active else ALPDirection.NONE
    out_reason = ALPReason.SLOW_LEAD if self.suggestion_active else reason
    self.decision = ALPDecision(active=self.suggestion_active, direction=out_dir,
                                reason=out_reason, candidate=candidate,
                                on_timer=self.on_timer, cooldown_remaining=self.cooldown_timer)
    return self.decision

  def notify_lane_change_started(self) -> None:
    """Call when any lane change begins so we don't immediately re-suggest."""
    self.cooldown_timer = C.COOLDOWN_TIME
    self.reset()
