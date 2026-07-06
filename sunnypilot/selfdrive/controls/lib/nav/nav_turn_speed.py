"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Navigation turn-speed controller.

When the active route has an upcoming maneuver that should be taken slower than the
current set speed (a turn, exit ramp, roundabout, U-turn), this outputs a speed
target that ramps the car down comfortably so it arrives at the maneuver at a sane
speed -- instead of carrying highway speed into a turn. It plugs into the
longitudinal planner as one more `LongitudinalPlanSource`, and the planner takes the
slowest of all sources, so it only ever *reduces* speed, never raises it.

The math (`maneuver_target_speed`, `allowed_speed`) is IO-free for unit testing.
"""
import math

from cereal import custom
from openpilot.common.params import Params
from openpilot.common.realtime import DT_MDL
from openpilot.selfdrive.car.cruise import V_CRUISE_UNSET
from openpilot.sunnypilot import PARAMS_UPDATE_PERIOD

NavTurnState = custom.LongitudinalPlanSP.NavTurn.NavTurnState

# Comfortable deceleration budget used to plan the slowdown (matches the map curve
# controller's target). Keep gentle -- this is assistance, the driver still steers.
COMFORT_DECEL = 1.2          # m/s^2
TARGET_OFFSET_T = 1.0        # s, reach the target a touch early to avoid overshoot
NAV_MIN_TARGET = 4.0         # m/s (~9 mph) floor; never command a crawl for a maneuver

NO_CONSTRAINT = 1000.0       # m/s sentinel: this maneuver needs no slowdown

# Target speeds (m/s) to be at when entering each maneuver type. Conservative.
_UTURN = 4.5        # ~10 mph
_SHARP = 6.7        # ~15 mph
_TURN = 9.0         # ~20 mph
_ROUNDABOUT = 8.0   # ~18 mph
_RAMP = 11.0        # ~25 mph, off-ramp / exit
_SLIGHT = 13.4      # ~30 mph


def maneuver_target_speed(maneuver_type: str, modifier: str) -> float:
  """Speed (m/s) we want to be at for the upcoming maneuver, or NO_CONSTRAINT."""
  t = (maneuver_type or "").lower()
  m = (modifier or "").lower()

  if "roundabout" in t or "rotary" in t:
    return _ROUNDABOUT
  if "uturn" in m:
    return _UTURN
  if "off ramp" in t or t == "exit":
    return _SHARP if "sharp" in m else _RAMP
  if "sharp" in m:
    return _SHARP
  if "slight" in m:
    return _SLIGHT
  if "left" in m or "right" in m:
    # a plain turn only matters for maneuver types that actually change heading
    if t in ("turn", "end of road", "fork", "continue", "new name", "merge", "on ramp"):
      return _TURN
  return NO_CONSTRAINT


def allowed_speed(target_v: float, distance: float,
                  decel: float = COMFORT_DECEL, offset_t: float = TARGET_OFFSET_T) -> float:
  """Max speed we can travel now and still reach target_v by the maneuver, at `decel`.

  v_allowed = sqrt(target_v^2 + 2 * decel * d_eff), d_eff = distance - target_v * offset_t.
  """
  d_eff = max(distance - target_v * offset_t, 0.0)
  return math.sqrt(target_v * target_v + 2.0 * decel * d_eff)


class NavTurnSpeedController:
  output_v_target: float = V_CRUISE_UNSET
  output_a_target: float = 0.0

  def __init__(self):
    self.params = Params()
    self.frame = -1
    self.enabled = self.params.get_bool("NavSlowdownEnabled")

    self.state = NavTurnState.disabled
    self.is_enabled = False
    self.is_active = False

    self.v_ego = 0.0
    self.a_ego = 0.0
    self.v_cruise = 0.0
    self.maneuver_distance = 0.0
    self.target_speed = 0.0

  def update_params(self) -> None:
    if self.frame % int(PARAMS_UPDATE_PERIOD / DT_MDL) == 0:
      self.enabled = self.params.get_bool("NavSlowdownEnabled")

  def _read_instruction(self, sm) -> tuple[bool, str, str, float]:
    valid = bool(sm.alive["navInstructionSP"] and sm.valid["navInstructionSP"])
    if not valid:
      return False, "", "", 0.0
    inst = sm["navInstructionSP"]
    return True, inst.maneuverType, inst.maneuverModifier, float(inst.maneuverDistance)

  def update(self, sm, long_enabled: bool, long_override: bool,
             v_ego: float, a_ego: float, v_cruise: float) -> None:
    self.frame += 1
    self.update_params()
    self.v_ego = v_ego
    self.a_ego = a_ego
    self.v_cruise = v_cruise

    have_route, mtype, modifier, distance = self._read_instruction(sm)
    self.output_a_target = a_ego

    # Fully off: not enabled, not engaged, or no active route.
    if not self.enabled or not long_enabled or not have_route:
      self.state = NavTurnState.disabled
      self.is_enabled = self.is_active = False
      self.output_v_target = V_CRUISE_UNSET
      self.maneuver_distance = 0.0
      self.target_speed = 0.0
      return

    self.is_enabled = True
    self.maneuver_distance = distance
    self.target_speed = maneuver_target_speed(mtype, modifier)

    if long_override:
      self.state = NavTurnState.overriding
      self.is_active = False
      self.output_v_target = V_CRUISE_UNSET
      return

    # No maneuver to slow for, or it's not actually slower than our set speed.
    if self.target_speed >= NO_CONSTRAINT or distance <= 0.0:
      self.state = NavTurnState.enabled
      self.is_active = False
      self.output_v_target = V_CRUISE_UNSET
      return

    v_allowed = max(allowed_speed(self.target_speed, distance), NAV_MIN_TARGET)

    if v_allowed < v_cruise:
      self.state = NavTurnState.slowing
      self.is_active = True
      self.output_v_target = v_allowed
    else:
      self.state = NavTurnState.enabled
      self.is_active = False
      self.output_v_target = V_CRUISE_UNSET
