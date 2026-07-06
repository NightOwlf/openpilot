"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Onroad turn-by-turn card: shows the next maneuver (direction arrow + distance +
street/instruction text) from navInstructionSP, published by sunnypilot navd.
"""
import pyray as rl

from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached
from openpilot.system.ui.widgets import Widget

CARD_W = 520
CARD_H = 170
ARROW_BOX = 150
MARGIN = 30

METER_TO_FOOT = 3.28084
METER_TO_MILE = 0.000621371
FOOT_ROUND = 10


def _format_distance(meters: float, is_metric: bool) -> str:
  if is_metric:
    if meters < 1000:
      return f"{int(round(meters / 10.0) * 10)} m"
    return f"{meters / 1000.0:.1f} km"
  feet = meters * METER_TO_FOOT
  if feet < 1000:
    return f"{int(round(feet / FOOT_ROUND) * FOOT_ROUND)} ft"
  return f"{meters * METER_TO_MILE:.1f} mi"


def _direction(modifier: str, mtype: str) -> str:
  m = (modifier or "").lower()
  t = (mtype or "").lower()
  if "uturn" in m or "uturn" in t:
    return "uturn"
  if "left" in m:
    return "left"
  if "right" in m:
    return "right"
  return "straight"


class NavInstructionRenderer(Widget):
  def __init__(self):
    super().__init__()
    self.font_bold = gui_app.font(FontWeight.BOLD)
    self.font_demi = gui_app.font(FontWeight.SEMI_BOLD)
    self.valid = False
    self.primary = ""
    self.distance_text = ""
    self.direction = "straight"

  def update(self):
    sm = ui_state.sm
    self.valid = bool(sm.alive["navInstructionSP"] and sm.valid["navInstructionSP"])
    if not self.valid:
      return
    inst = sm["navInstructionSP"]
    self.primary = inst.maneuverPrimaryText
    self.valid = bool(self.primary)
    if not self.valid:
      return
    self.distance_text = _format_distance(float(inst.maneuverDistance), ui_state.is_metric)
    self.direction = _direction(inst.maneuverModifier, inst.maneuverType)

  def _draw_arrow(self, box: rl.Rectangle):
    cx, cy = box.x + box.width / 2, box.y + box.height / 2
    s = box.width * 0.30
    color = rl.Color(255, 255, 255, 255)
    if self.direction in ("left", "right"):
      sign = -1 if self.direction == "left" else 1
      # horizontal shaft + arrowhead
      rl.draw_rectangle_rounded(rl.Rectangle(cx - s * 0.15, cy - s * 0.18, s * 0.9, s * 0.36), 0.5, 6, color)
      tip = rl.Vector2(cx + sign * s, cy)
      a = rl.Vector2(cx + sign * s * 0.35, cy - s * 0.75)
      b = rl.Vector2(cx + sign * s * 0.35, cy + s * 0.75)
      # wind triangle CW regardless of side
      if sign > 0:
        rl.draw_triangle(a, b, tip, color)
      else:
        rl.draw_triangle(b, a, tip, color)
    elif self.direction == "uturn":
      rl.draw_ring(rl.Vector2(cx, cy), s * 0.55, s * 0.85, 90, 270, 32, color)
      tip = rl.Vector2(cx - s * 0.7, cy + s * 0.9)
      rl.draw_triangle(rl.Vector2(cx - s * 0.7 - s * 0.4, cy + s * 0.2),
                       rl.Vector2(cx - s * 0.7 + s * 0.4, cy + s * 0.2), tip, color)
    else:  # straight
      rl.draw_rectangle_rounded(rl.Rectangle(cx - s * 0.18, cy - s * 0.15, s * 0.36, s * 0.95), 0.5, 6, color)
      tip = rl.Vector2(cx, cy - s)
      rl.draw_triangle(rl.Vector2(cx - s * 0.75, cy - s * 0.35 + s * 0.05),
                       rl.Vector2(cx + s * 0.75, cy - s * 0.35 + s * 0.05), tip, color)

  def _render(self, rect: rl.Rectangle):
    if not self.valid:
      return

    x = rect.x + MARGIN + 40
    y = rect.y + MARGIN + 180  # below the set-speed cluster
    card = rl.Rectangle(x, y, CARD_W, CARD_H)
    rl.draw_rectangle_rounded(card, 0.15, 12, rl.Color(0, 0, 0, 180))

    self._draw_arrow(rl.Rectangle(card.x + 10, card.y + (CARD_H - ARROW_BOX) / 2, ARROW_BOX, ARROW_BOX))

    text_x = card.x + ARROW_BOX + 30
    # distance (large)
    dsz = measure_text_cached(self.font_bold, self.distance_text, 66)
    rl.draw_text_ex(self.font_bold, self.distance_text, rl.Vector2(text_x, card.y + 28), 66, 0, rl.WHITE)

    # instruction text (elide to fit)
    text = self.primary
    max_w = card.width - (ARROW_BOX + 60)
    tsz = measure_text_cached(self.font_demi, text, 40)
    while tsz.x > max_w and len(text) > 3:
      text = text[:-1]
      tsz = measure_text_cached(self.font_demi, text + "...", 40)
    if text != self.primary:
      text = text + "..."
    rl.draw_text_ex(self.font_demi, text, rl.Vector2(text_x, card.y + 28 + dsz.y + 12), 40, 0,
                    rl.Color(255, 255, 255, 220))
