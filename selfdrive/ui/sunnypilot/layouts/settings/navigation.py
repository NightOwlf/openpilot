"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Navigation settings: Mapbox token entry, address search (DFW-biased) that sets the
navigation destination, Home/Work favorites, and stop navigation.
"""
import json

from openpilot.common.params import Params
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr
from openpilot.system.ui.sunnypilot.widgets.input_dialog import InputDialogSP
from openpilot.system.ui.sunnypilot.widgets.list_view import button_item_sp, dual_button_item_sp, toggle_item_sp, \
  LineSeparatorSP
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.option_dialog import MultiOptionDialog
from openpilot.system.ui.widgets.scroller_tici import Scroller

from openpilot.sunnypilot.navd.geocode import geocode


class NavigationLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = Params()
    self._search_results = []
    self._pending_fav: str | None = None      # favorite the current search saves to (None = navigate now)
    self._results_dialog: MultiOptionDialog | None = None

    items = self._initialize_items()
    self._scroller = Scroller(items, line_separator=True, spacing=0)

  # --- param helpers ---
  def _token(self) -> str:
    return self._params.get("MapboxToken", encoding="utf8") or ""

  def _place_name(self, param: str, empty: str) -> str:
    raw = self._params.get(param, encoding="utf8")
    if not raw:
      return empty
    try:
      return json.loads(raw).get("place_name", tr("Custom location"))
    except (ValueError, TypeError):
      return tr("Custom location")

  def _navigating(self) -> bool:
    return bool(self._params.get("NavDestination", encoding="utf8"))

  # --- items ---
  def _initialize_items(self):
    token_item = button_item_sp(
      title=lambda: tr("Mapbox Token"),
      button_text=lambda: tr("Change") if self._token() else tr("Set"),
      description=lambda: tr("Required for navigation. Get a free access token at mapbox.com."),
      callback=self._edit_token,
    )
    search_item = button_item_sp(
      title=lambda: tr("Search & Navigate"),
      button_text=lambda: tr("Search"),
      description=lambda: tr("Destination: ") + self._place_name("NavDestination", tr("None")),
      callback=self._search_destination,
      enabled=lambda: bool(self._token()),
    )
    home_item = dual_button_item_sp(
      left_text=lambda: tr("Go Home"),
      right_text=lambda: tr("Set Home"),
      left_callback=lambda: self._go_favorite("NavFavoriteHome"),
      right_callback=lambda: self._set_favorite("NavFavoriteHome", tr("Set Home")),
      description=lambda: tr("Home: ") + self._place_name("NavFavoriteHome", tr("not set")),
      enabled=lambda: bool(self._token()),
    )
    work_item = dual_button_item_sp(
      left_text=lambda: tr("Go Work"),
      right_text=lambda: tr("Set Work"),
      left_callback=lambda: self._go_favorite("NavFavoriteWork"),
      right_callback=lambda: self._set_favorite("NavFavoriteWork", tr("Set Work")),
      description=lambda: tr("Work: ") + self._place_name("NavFavoriteWork", tr("not set")),
      enabled=lambda: bool(self._token()),
    )
    stop_item = button_item_sp(
      title=lambda: tr("Stop Navigation"),
      button_text=lambda: tr("Stop"),
      callback=self._stop_navigation,
      enabled=lambda: self._navigating(),
    )
    slowdown_item = toggle_item_sp(
      title=lambda: tr("Slow for Turns & Exits"),
      description=lambda: tr("Automatically ease off the throttle and slow down for upcoming turns, exit ramps, "
                            "and roundabouts on the active route. You still steer the maneuver."),
      param="NavSlowdownEnabled",
    )
    return [
      token_item, LineSeparatorSP(40),
      search_item, LineSeparatorSP(40),
      home_item, work_item, LineSeparatorSP(40),
      stop_item, LineSeparatorSP(40),
      slowdown_item,
    ]

  # --- callbacks ---
  def _edit_token(self):
    InputDialogSP(
      tr("Mapbox Token"),
      sub_title=tr("Paste your Mapbox public access token"),
      current_text=self._token(),
      param="MapboxToken",
    ).show()

  def _search_destination(self):
    if not self._token():
      return
    self._pending_fav = None
    self._open_search(tr("Search Destination"))

  def _set_favorite(self, param: str, title: str):
    if not self._token():
      return
    self._pending_fav = param
    self._open_search(title)

  def _open_search(self, title: str):
    InputDialogSP(
      title,
      sub_title=tr("Enter an address or place in the DFW area"),
      callback=self._on_query,
    ).show()

  def _on_query(self, result: DialogResult, text: str):
    if result != DialogResult.CONFIRM or not text.strip():
      return
    self._search_results = geocode(text, self._token())
    if not self._search_results:
      return
    options = [p.name for p in self._search_results]
    self._results_dialog = MultiOptionDialog(tr("Select Destination"), options, callback=self._on_pick)
    gui_app.push_widget(self._results_dialog)

  def _on_pick(self, result: DialogResult):
    if result != DialogResult.CONFIRM or self._results_dialog is None:
      return
    name = self._results_dialog.selection
    place = next((p for p in self._search_results if p.name == name), None)
    if place is None:
      return
    target = self._pending_fav or "NavDestination"
    self._params.put(target, place.to_destination_json())

  def _go_favorite(self, param: str):
    raw = self._params.get(param, encoding="utf8")
    if raw:
      self._params.put("NavDestination", raw)

  def _stop_navigation(self):
    self._params.remove("NavDestination")

  # --- widget ---
  def _render(self, rect):
    self._scroller.render(rect)

  def show_event(self):
    self._scroller.show_event()
