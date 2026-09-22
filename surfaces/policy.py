"""Pure per-surface encode decisions. All time/history is explicit input/output."""

import math
from dataclasses import dataclass, replace

from .model import Size, Surface, ViewHint, Visibility


@dataclass(frozen=True)
class History:
    lane: str = "cold"
    frozen: bool = False
    high_damage_since: float | None = None
    low_damage_since: float | None = None
    small_since: float | None = None


@dataclass(frozen=True)
class Decision:
    history: History
    target: Size
    lane: str
    frozen: bool
    refresh: bool
    repack: bool
    qp_delta: int
    max_updates_hz: float | None


def decide(
    surface: Surface,
    hint: ViewHint | None,
    damage_fraction: float,
    tile_size: Size,
    history: History,
    now: float,
    hot_allowlist: frozenset[str] = frozenset(),
) -> Decision:
    """Timers only advance while their condition remains continuously true.

    Returned lane/target are requests; the caller commits them at a repack.
    History.lane remains the actual lane until the caller commits migration.
    """
    if (
        not math.isfinite(now)
        or not math.isfinite(damage_fraction)
        or not 0 <= damage_fraction <= 1
    ):
        raise ValueError("invalid policy clock or damage fraction")
    if history.lane not in ("hot", "cold"):
        raise ValueError("invalid lane")
    if hint is not None and hint.surface_id != surface.surface_id:
        raise ValueError("hint belongs to another surface")
    frozen = hint is None or hint.visible in (Visibility.HIDDEN, Visibility.OUT_OF_VIEW)
    if frozen:
        return Decision(
            replace(
                history,
                frozen=True,
                high_damage_since=None,
                low_damage_since=None,
                small_since=None,
            ),
            tile_size,
            history.lane,
            True,
            False,
            False,
            0,
            0,
        )
    target = Size(
        min(surface.size.w, max(1, math.ceil(hint.display_px.w * 1.25))),
        min(surface.size.h, max(1, math.ceil(hint.display_px.h * 1.25))),
    )
    high = history.high_damage_since if damage_fraction > 0.30 else None
    low = history.low_damage_since if damage_fraction < 0.05 else None
    if damage_fraction > 0.30 and high is None:
        high = now
    if damage_fraction < 0.05 and low is None:
        low = now
    lane = history.lane
    if surface.app_id in hot_allowlist or (high is not None and now - high > 1):
        lane = "hot"
    elif low is not None and now - low >= 3:
        lane = "cold"
    smaller = target.w < tile_size.w * 0.7 and target.h < tile_size.h * 0.7
    small = (
        (history.small_since if history.small_since is not None else now)
        if smaller
        else None
    )
    resize = (
        (small is not None and now - small > 2)
        or target.w > tile_size.w * 1.1
        or target.h > tile_size.h * 1.1
    )
    peripheral = hint.visible == Visibility.PERIPHERAL
    qp = (8 if peripheral else 0) + (0 if hint.focused else 4)
    qp += round((255 - hint.fovea) * 4 / 255)
    qp -= round((hint.priority - 128) * 4 / 128)
    qp = max(0, min(16, qp))
    return Decision(
        History(history.lane, False, high, low, small),
        target,
        lane,
        False,
        history.frozen,
        resize or lane != history.lane,
        qp,
        5 if lane == "cold" and peripheral else None,
    )


def split_budget(available_bps: int, cold_demand_bps: int) -> tuple[int, int]:
    """Cold bytes consume the available media budget before hot bytes."""
    if available_bps < 0 or cold_demand_bps < 0:
        raise ValueError("negative bandwidth")
    cold = min(available_bps, cold_demand_bps)
    return cold, available_bps - cold
