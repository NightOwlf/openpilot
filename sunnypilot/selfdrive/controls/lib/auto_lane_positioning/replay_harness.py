#!/usr/bin/env python3
"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Auto Lane Positioning -- replay/shadow harness.

Replays a logged drive through the *real* AutoLanePositioningController at the true
~20 Hz model cadence and reports, without ever touching the car:
  * every point where a pass would have been *suggested* (ASSIST prompt),
  * a histogram of why it was suppressed the rest of the time,
  * whether blind-spot data was ever present (safety-relevant on cars w/o BSM).

This is how we tune the thresholds to how you actually drive BEFORE trusting the
feature -- and it is the prerequisite for ever building a self-executing AUTO mode.

Needs the built openpilot stack (compiled cereal), so run it on the comma device
itself or in a Linux dev container -- not on a bare macOS checkout.

Usage:
  # a route from your comma connect account (auth once via tools/lib/auth.py):
  ./replay_harness.py "<dongle_id>|<route>"
  # a single segment, or a local rlog path also work (anything LogReader accepts):
  ./replay_harness.py "<dongle_id>|<route>--0"
  ./replay_harness.py /data/media/0/realdata/<route>--0/rlog
"""
import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field

from openpilot.common.realtime import DT_MDL
from openpilot.tools.lib.logreader import LogReader
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_positioning.auto_lane_positioning import (
  AutoLanePositioningController,
  ALPInput,
)
from openpilot.sunnypilot.selfdrive.controls.lib.auto_lane_positioning.constants import (
  ALPDirection,
  ALPMode,
  ALPReason,
)

MS_TO_MPH = 2.23694

MODE_BY_NAME = {"off": ALPMode.OFF, "assist": ALPMode.ASSIST, "auto": ALPMode.AUTO}
DIRECTION_NAME = {ALPDirection.NONE: "none", ALPDirection.LEFT: "LEFT", ALPDirection.RIGHT: "RIGHT"}
REASON_NAME = {v: k for k, v in vars(ALPReason).items() if isinstance(v, int) and not k.startswith("_")}


@dataclass
class Prompt:
  t: float           # seconds since first model frame
  direction: int
  v_ego_mph: float
  set_speed_mph: float
  lead_d_rel: float
  lead_v_mph: float


@dataclass
class ReplayResult:
  route: str = ""
  model_frames: int = 0
  duration_s: float = 0.0
  prompts: list[Prompt] = field(default_factory=list)
  reason_frames: Counter = field(default_factory=Counter)   # reason -> frame count (suppression breakdown)
  candidate_frames: int = 0                                  # frames instantaneously qualifying
  bsm_ever_seen: bool = False


def run_replay(identifier: str, mode: int = ALPMode.ASSIST) -> ReplayResult:
  lr = LogReader(identifier, sort_by_time=True)

  ctrl = AutoLanePositioningController()
  ctrl.mode = mode  # drive the mode directly; don't depend on the on-device param

  res = ReplayResult(route=identifier)

  # latest-seen snapshots of the services the controller needs
  cs = None
  rs = None
  cc = None
  t0 = None

  for msg in lr:
    typ = msg.which()
    if typ == "carState":
      cs = msg.carState
    elif typ == "radarState":
      rs = msg.radarState
    elif typ == "carControl":
      cc = msg.carControl
    elif typ == "modelV2":
      if cs is None:
        continue
      if t0 is None:
        t0 = msg.logMonoTime
      t = (msg.logMonoTime - t0) / 1e9

      lead = rs.leadOne if rs is not None else None
      lat_active = bool(cc.latActive) if cc is not None else False
      left_bs = bool(cs.leftBlindspot)
      right_bs = bool(cs.rightBlindspot)
      res.bsm_ever_seen = res.bsm_ever_seen or left_bs or right_bs

      inp = ALPInput(
        lateral_active=lat_active,
        v_ego=max(float(cs.vEgo), 0.0),
        v_cruise_setpoint=float(cs.cruiseState.speed),
        lead_status=bool(lead.status) if lead is not None else False,
        lead_d_rel=float(lead.dRel) if lead is not None else 0.0,
        lead_v_lead=float(lead.vLead) if lead is not None else 0.0,
        lead_prob=float(lead.modelProb) if lead is not None else 0.0,
        left_blindspot=left_bs,
        right_blindspot=right_bs,
        lane_change_active=False,  # replay approximation: we don't re-run the LC state machine here
        brake_pressed=bool(cs.brakePressed),
        steering_pressed=bool(cs.steeringPressed),
      )

      was_active = ctrl.suggestion_active
      dec = ctrl.evaluate(inp)

      res.model_frames += 1
      res.duration_s = t
      res.reason_frames[dec.reason] += 1
      if dec.candidate:
        res.candidate_frames += 1

      # rising edge of an active suggestion == one prompt the driver would have seen
      if dec.active and not was_active:
        res.prompts.append(Prompt(
          t=t,
          direction=dec.direction,
          v_ego_mph=inp.v_ego * MS_TO_MPH,
          set_speed_mph=inp.v_cruise_setpoint * MS_TO_MPH,
          lead_d_rel=inp.lead_d_rel,
          lead_v_mph=inp.lead_v_lead * MS_TO_MPH,
        ))

  return res


def _fmt_ts(t: float) -> str:
  return f"{int(t // 60):d}:{t % 60:04.1f}"


def print_report(res: ReplayResult) -> None:
  print(f"\n=== Auto Lane Positioning replay: {res.route} ===")
  print(f"model frames: {res.model_frames}  (~{res.duration_s / 60:.1f} min)")
  bsm_note = "YES" if res.bsm_ever_seen else "NO -- BSM never seen; side-clear check is blind, tune conservatively!"
  print(f"blind-spot data present in log: {bsm_note}")
  qualified_s = res.candidate_frames * DT_MDL
  print(f"instantaneously-qualifying time: {qualified_s:.1f}s "
        f"({100.0 * res.candidate_frames / max(res.model_frames, 1):.1f}% of drive)")

  print(f"\nprompts that would have fired: {len(res.prompts)}")
  for i, p in enumerate(res.prompts, 1):
    print(f"  {i:2d}. t={_fmt_ts(p.t):>7}  {DIRECTION_NAME.get(p.direction, '?'):>5}  "
          f"ego={p.v_ego_mph:5.1f}mph  set={p.set_speed_mph:5.1f}mph  "
          f"lead {p.lead_d_rel:5.1f}m @ {p.lead_v_mph:5.1f}mph")

  print("\nsuppression / reason breakdown (share of frames):")
  for reason, n in res.reason_frames.most_common():
    pct = 100.0 * n / max(res.model_frames, 1)
    print(f"  {REASON_NAME.get(reason, str(reason)):>18}: {pct:5.1f}%  ({n} frames)")


def main() -> int:
  parser = argparse.ArgumentParser(description="Replay a drive through the Auto Lane Positioning planner.")
  parser.add_argument("identifier", help="route/segment id (comma connect) or local rlog path")
  parser.add_argument("--mode", choices=list(MODE_BY_NAME), default="assist",
                      help="ALP mode to simulate (default: assist)")
  args = parser.parse_args()

  res = run_replay(args.identifier, MODE_BY_NAME[args.mode])
  print_report(res)
  return 0


if __name__ == "__main__":
  sys.exit(main())
