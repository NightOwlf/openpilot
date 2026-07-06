#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

sunnypilot navd -- turn-by-turn navigation daemon.

Reads a destination (NavDestination param) + live GPS, fetches a route from the
Mapbox Directions API, tracks progress along it, and publishes navInstructionSP
(next maneuver, distances, lanes, ETA) and navRouteSP (route geometry) for the UI
and, later, the driving planner (exit/curve slowdown, lane guidance).

Requires a user-supplied Mapbox token in the MapboxToken param. If unset, the
daemon idles and publishes an invalid instruction (nav simply off).
"""
import os

import requests

from cereal import messaging
from openpilot.common.params import Params
from openpilot.common.realtime import Ratekeeper
from openpilot.common.swaglog import cloudlog
from openpilot.sunnypilot.navd.helpers import Coordinate, coordinate_from_param
from openpilot.sunnypilot.navd.route_engine import Route

# Rate-limit backoff (in 1 Hz ticks) between route (re)computations.
RECOMPUTE_TICKS = 10
# Consider the destination reached within this distance (m).
ARRIVAL_DISTANCE = 25.0

MAPBOX_BASE = os.environ.get("MAPBOX_BASE", "https://api.mapbox.com")


class RouteEngine:
  def __init__(self, sm: messaging.SubMaster, pm: messaging.PubMaster):
    self.sm = sm
    self.pm = pm
    self.params = Params()

    self.route = Route()
    self.last_position: Coordinate | None = None
    self.last_bearing: float = 0.0
    self.gps_ok = False

    self.nav_destination: Coordinate | None = None
    self.recompute_countdown = 0

    self.mapbox_token = self.params.get("MapboxToken", encoding="utf8") or ""

  # --- main tick ---
  def update(self) -> None:
    self.sm.update(0)
    self.update_location()
    self.recompute_route()
    self.send_instruction()

  def update_location(self) -> None:
    loc = self.sm["gpsLocationExternal"]
    self.gps_ok = self.sm.valid["gpsLocationExternal"] and (loc.flags % 2 == 1)
    if self.gps_ok:
      self.last_position = Coordinate(loc.latitude, loc.longitude)
      self.last_bearing = float(loc.bearingDeg)

  def recompute_route(self) -> None:
    if self.last_position is None:
      return

    new_destination = coordinate_from_param("NavDestination", self.params)
    if new_destination is None:
      self.clear_route()
      return

    should_recompute = self.nav_destination is None or self.nav_destination != new_destination
    if self.route.valid and self.route._off_route(self.last_position):
      should_recompute = True

    if self.recompute_countdown > 0:
      self.recompute_countdown -= 1
      return

    if should_recompute:
      self.recompute_countdown = RECOMPUTE_TICKS
      self.calculate_route(new_destination)

  def clear_route(self) -> None:
    if self.nav_destination is not None:
      cloudlog.info("navd: destination cleared")
    self.route = Route()
    self.nav_destination = None

  # --- routing ---
  def calculate_route(self, destination: Coordinate) -> None:
    if not self.mapbox_token:
      # refresh in case the user just set it
      self.mapbox_token = self.params.get("MapboxToken", encoding="utf8") or ""
      if not self.mapbox_token:
        cloudlog.warning("navd: no MapboxToken set; cannot route")
        return

    cloudlog.info(f"navd: computing route to {destination}")
    self.nav_destination = destination

    coords = f"{self.last_position.longitude},{self.last_position.latitude};" \
             f"{destination.longitude},{destination.latitude}"
    url = f"{MAPBOX_BASE}/directions/v5/mapbox/driving-traffic/{coords}"
    query = {
      "access_token": self.mapbox_token,
      "geometries": "geojson",
      "overview": "full",
      "steps": "true",
      "banner_instructions": "true",
      "annotations": "maxspeed,distance,duration",
      "alternatives": "false",
      "voice_instructions": "false",
      # depart in the direction we're actually heading to avoid immediate U-turns
      "bearings": f"{int(self.last_bearing)},90;",
    }

    try:
      resp = requests.get(url, params=query, timeout=10)
      resp.raise_for_status()
      self.route = Route.from_mapbox_json(resp.json())
      self.send_route()
    except requests.exceptions.RequestException as e:
      cloudlog.warning(f"navd: route request failed: {e}")
    except (KeyError, IndexError, ValueError) as e:
      cloudlog.warning(f"navd: could not parse route response: {e}")

  # --- publishing ---
  def send_route(self) -> None:
    msg = messaging.new_message("navRouteSP")
    coords = msg.navRouteSP.init("coordinates", len(self.route.full_geometry))
    for i, c in enumerate(self.route.full_geometry):
      coords[i].latitude = float(c.latitude)
      coords[i].longitude = float(c.longitude)
    self.pm.send("navRouteSP", msg)

  def send_instruction(self) -> None:
    msg = messaging.new_message("navInstructionSP")

    if not self.route.valid or self.last_position is None:
      msg.valid = False
      self.pm.send("navInstructionSP", msg)
      return

    inst = self.route.update_progress(self.last_position)
    msg.valid = inst.valid

    i = msg.navInstructionSP
    i.maneuverPrimaryText = inst.maneuver_primary_text
    i.maneuverSecondaryText = inst.maneuver_secondary_text
    i.maneuverType = inst.maneuver_type
    i.maneuverModifier = inst.maneuver_modifier
    i.maneuverDistance = float(inst.maneuver_distance)
    i.distanceRemaining = float(inst.distance_remaining)
    i.timeRemaining = float(inst.time_remaining)
    i.timeRemainingTypical = float(inst.time_remaining_typical)
    i.speedLimit = float(inst.speed_limit)
    i.showFull = bool(inst.show_full)

    if inst.lanes:
      lanes = i.init("lanes", len(inst.lanes))
      for idx, lane in enumerate(inst.lanes):
        lanes[idx].active = bool(lane.get("active", False))
        directions = lane.get("directions", [])
        dirs = lanes[idx].init("directions", len(directions))
        for k, d in enumerate(directions):
          dirs[k] = d
        if "activeDirection" in lane:
          lanes[idx].activeDirection = lane["activeDirection"]

    self.pm.send("navInstructionSP", msg)

    # Arrived: clear the destination so nav turns off.
    if inst.distance_remaining < ARRIVAL_DISTANCE:
      cloudlog.info("navd: destination reached")
      self.params.remove("NavDestination")
      self.clear_route()


def main() -> None:
  sm = messaging.SubMaster(["gpsLocationExternal"])
  pm = messaging.PubMaster(["navInstructionSP", "navRouteSP"])

  route_engine = RouteEngine(sm, pm)
  rk = Ratekeeper(1.0, print_delay_threshold=None)
  while True:
    route_engine.update()
    rk.keep_time()


if __name__ == "__main__":
  main()
