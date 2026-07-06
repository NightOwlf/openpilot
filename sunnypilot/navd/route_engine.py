"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Route parsing + on-route progress math for sunnypilot navd.

This module is deliberately IO-free (no GPS, HTTP, params, or cereal publishing) so
it can be unit tested and reasoned about on its own. `navd.py` owns all the IO and
feeds parsed Mapbox Directions JSON into `Route`, then asks it for progress.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from openpilot.sunnypilot.navd.helpers import (
  Coordinate,
  distance_along_geometry,
  maxspeed_to_ms,
  parse_banner_instructions,
)

# How close (m) to a maneuver before we consider it "passed" and advance to the next step.
REROUTE_DISTANCE = 25.0
MANEUVER_TRANSITION_THRESHOLD = 10.0
# If we drift this far from the route geometry, we're off-route and should reroute.
OFF_ROUTE_DISTANCE = 40.0


@dataclass
class Step:
  distance: float                       # length of this step (m)
  duration: float                       # nominal duration (s)
  duration_typical: float               # duration without live traffic (s)
  geometry: list[Coordinate]            # densified geometry for this step
  banners: list                         # raw Mapbox bannerInstructions for this step
  maxspeed_ms: float = 0.0              # posted limit on this step (m/s, 0 = unknown)


@dataclass
class Instruction:
  """Flat, publish-ready snapshot of where the driver is on the route."""
  valid: bool = False
  maneuver_primary_text: str = ""
  maneuver_secondary_text: str = ""
  maneuver_type: str = ""
  maneuver_modifier: str = ""
  maneuver_distance: float = 0.0        # m to next maneuver
  distance_remaining: float = 0.0       # m to destination
  time_remaining: float = 0.0           # s to destination (with traffic)
  time_remaining_typical: float = 0.0   # s to destination (no traffic)
  speed_limit: float = 0.0              # m/s
  show_full: bool = False
  lanes: list = field(default_factory=list)
  off_route: bool = False


def _annotation_maxspeed_ms(annotation: dict, seg_idx: int) -> float:
  """Mapbox `maxspeed` is per-geometry-segment; take the one at seg_idx if present."""
  speeds = annotation.get("maxspeed") if annotation else None
  if not speeds or seg_idx >= len(speeds):
    return 0.0
  ms = speeds[seg_idx]
  if not ms or ms.get("unknown") or ms.get("none") or "speed" not in ms:
    return 0.0
  try:
    return maxspeed_to_ms({"unit": ms.get("unit", "mph"), "speed": ms["speed"]})
  except (KeyError, TypeError):
    return 0.0


class Route:
  """A parsed Mapbox route plus stateful progress tracking along it."""

  def __init__(self):
    self.steps: list[Step] = []
    self.full_geometry: list[Coordinate] = []
    self.step_idx: int | None = None

  @property
  def valid(self) -> bool:
    return self.step_idx is not None and 0 <= self.step_idx < len(self.steps)

  @classmethod
  def from_mapbox_json(cls, resp: dict) -> Route:
    """Build a Route from a Mapbox Directions API response (geometries=geojson)."""
    route = cls()
    routes = resp.get("routes") or []
    if not routes:
      return route

    leg = routes[0]["legs"][0]
    annotation = leg.get("annotation", {})

    seg_idx = 0
    for step in leg["steps"]:
      coords = [Coordinate.from_mapbox_tuple(c) for c in step["geometry"]["coordinates"]]
      n_segs = max(len(coords) - 1, 1)
      route.steps.append(Step(
        distance=float(step.get("distance", 0.0)),
        duration=float(step.get("duration", 0.0)),
        duration_typical=float(step.get("duration_typical", step.get("duration", 0.0))),
        geometry=coords,
        banners=step.get("bannerInstructions", []),
        maxspeed_ms=_annotation_maxspeed_ms(annotation, seg_idx),
      ))
      route.full_geometry.extend(coords)
      seg_idx += n_segs

    route.step_idx = 0 if route.steps else None
    return route

  def distance_to_destination(self, pos: Coordinate) -> float:
    if not self.valid:
      return 0.0
    step = self.steps[self.step_idx]
    along = distance_along_geometry(step.geometry, pos)
    remaining = max(step.distance - along, 0.0)
    for s in self.steps[self.step_idx + 1:]:
      remaining += s.distance
    return remaining

  def _off_route(self, pos: Coordinate) -> bool:
    if not self.valid:
      return True
    step = self.steps[self.step_idx]
    # cheapest robust check: min distance to the current step geometry
    closest = min((pos.distance_to(c) for c in step.geometry), default=1e9)
    return closest > OFF_ROUTE_DISTANCE

  def update_progress(self, pos: Coordinate) -> Instruction:
    """Advance step index based on position and return a publish-ready Instruction."""
    if not self.valid:
      return Instruction(valid=False, off_route=True)

    # Advance past any maneuvers we've already driven through.
    while self.step_idx < len(self.steps) - 1:
      step = self.steps[self.step_idx]
      along = distance_along_geometry(step.geometry, pos)
      if step.distance - along < MANEUVER_TRANSITION_THRESHOLD:
        self.step_idx += 1
      else:
        break

    step = self.steps[self.step_idx]
    along = distance_along_geometry(step.geometry, pos)
    maneuver_distance = max(step.distance - along, 0.0)

    inst = Instruction(valid=True)
    inst.maneuver_distance = maneuver_distance
    inst.distance_remaining = self.distance_to_destination(pos)
    inst.speed_limit = step.maxspeed_ms
    inst.off_route = self._off_route(pos)

    # remaining time: scale current step by fraction left, add full later steps
    frac_left = (maneuver_distance / step.distance) if step.distance > 0 else 0.0
    inst.time_remaining = step.duration * frac_left
    inst.time_remaining_typical = step.duration_typical * frac_left
    for s in self.steps[self.step_idx + 1:]:
      inst.time_remaining += s.duration
      inst.time_remaining_typical += s.duration_typical

    # maneuver text / lanes from the banner instructions for this step
    banner = parse_banner_instructions(step.banners, maneuver_distance)
    if banner is not None:
      inst.maneuver_primary_text = banner.get("maneuverPrimaryText", "")
      inst.maneuver_secondary_text = banner.get("maneuverSecondaryText", "")
      inst.maneuver_type = banner.get("maneuverType", "")
      inst.maneuver_modifier = banner.get("maneuverModifier", "")
      inst.lanes = banner.get("lanes", [])
      inst.show_full = bool(banner.get("showFull", False))

    return inst
