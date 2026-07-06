"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Auto Lane Positioning (ALP) -- "automatic passing" decision constants.

These govern when the controller decides that the ego vehicle is being held up
by a slower lead and a pass to an adjacent lane is worth suggesting (ASSIST mode)
or initiating (AUTO mode). Values are intentionally conservative; they should be
tuned per platform on real logs before AUTO mode is enabled.
"""

from openpilot.common.constants import CV


class ALPMode:
  OFF = 0
  ASSIST = 1   # detect the opportunity, surface a suggestion; driver executes
  AUTO = 2     # detect and self-initiate the lane change (guarded)


class ALPDirection:
  NONE = 0
  LEFT = 1
  RIGHT = 2


class ALPReason:
  """Why a suggestion is (not) active -- for UI text and log/replay analysis."""
  NONE = 0
  SLOW_LEAD = 1            # actively suggesting: slower lead ahead, side clear
  # suppression reasons (no suggestion):
  DISABLED = 10
  LOW_SPEED = 11          # below highway threshold
  NO_LEAD = 12            # no stable lead to pass
  LEAD_NOT_SLOW = 13      # lead is not meaningfully slower than set speed
  LEAD_TOO_FAR = 14       # lead outside engagement band
  SIDE_BLOCKED = 15       # blindspot / adjacent lane occupied
  ALREADY_CHANGING = 16   # a lane change is already in progress
  DRIVER_OVERRIDE = 17    # brake / steering override active
  COOLDOWN = 18           # too soon after the previous maneuver/suggestion


class ALPConstants:
  # --- Speed gating (highway only) ---
  MIN_ENGAGE_SPEED = 40 * CV.MPH_TO_MS   # do not pass below this (US highway floor)

  # --- What counts as "held up by a slow lead" ---
  # The lead must be slower than our target (set) speed by at least this margin,
  # sustained, before we consider it worth passing.
  SLOWER_THAN_SET_MARGIN = 5 * CV.MPH_TO_MS

  # Engagement distance band. Too close = unsafe to start; too far = not held up yet.
  # Both scale with speed via the lookup tables below (breakpoints in m/s).
  ENGAGE_DIST_BP = [40 * CV.MPH_TO_MS, 60 * CV.MPH_TO_MS, 80 * CV.MPH_TO_MS]
  ENGAGE_DIST_MIN_V = [18., 24., 32.]    # m, nearest we will start a pass from
  ENGAGE_DIST_MAX_V = [70., 95., 130.]   # m, farthest a lead still "holds us up"

  # Lead must be stably detected (radar/vision agreement) to act on it.
  MIN_LEAD_PROB = 0.5

  # --- Debounce / hysteresis (in seconds) ---
  # A qualifying condition must persist this long before a suggestion turns on,
  # and the suggestion holds for a short time to avoid flicker.
  SUGGEST_ON_TIME = 1.5
  SUGGEST_HOLD_TIME = 0.6

  # Minimum gap between the end of one maneuver/suggestion and the next.
  COOLDOWN_TIME = 8.0

  # --- Side selection ---
  # In left-hand-drive (US) traffic, overtake on the left by default.
  # Set via traffic convention at the adapter boundary.
  DEFAULT_PASS_LEFT = True
