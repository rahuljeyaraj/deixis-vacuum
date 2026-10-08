"""A pretend robot vacuum with the same methods as `vacuum.CloudRobot`, for tests and dry runs
(`vacuum_mcp.py --fake`). No login, no cloud, nothing moves.

It starts on its dock. A zone clean sets the work mode (vacuum only, like the real robot) and
then drives it (at `speed_mm_s`, after `leave_s` waiting on the dock) to the zone and round its
edge; stop leaves it where it is; dock drives it home. Positions are in map millimetres, like
the real robot's.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable


class FakeRobot:
    def __init__(
        self,
        dock: tuple[float, float] = (24.0, 287.0),  # where rahul's N50 dock reported, 3 Oct
        speed_mm_s: float = 300.0,
        leave_s: float = 0.0,
        now: Callable[[], float] = time.monotonic,
        work_mode: str | None = "vacuum",
    ) -> None:
        self.dock_xy = dock
        self.speed = speed_mm_s
        self.leave_s = leave_s
        self.now = now
        self.work_mode = work_mode
        self.battery = 100
        self.orders: list[dict] = []  # every order received, in order
        self._route: list[tuple[float, float]] = [dock]  # where it goes, from _t0
        self._t0 = now()
        self._state = "docked"
        self._end_state = "docked"

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        pass

    # ---------------------------------------------------------------- motion

    def _position(self) -> tuple[float, float]:
        t = self.now() - self._t0
        if self._state == "cleaning":
            t -= self.leave_s
        t = max(0.0, t)
        dist = t * self.speed
        pts = self._route
        for a, b in zip(pts, pts[1:], strict=False):
            seg = math.dist(a, b)
            if dist <= seg:
                f = dist / seg if seg else 1.0
                return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)
            dist -= seg
        return pts[-1]

    def _done(self) -> bool:
        return self._position() == self._route[-1]

    def _go(self, route: list[tuple[float, float]], state: str, end_state: str) -> None:
        here = self._position()
        self._route = [here, *route]
        self._t0 = self.now()
        self._state, self._end_state = state, end_state

    # ---------------------------------------------------------------- orders

    async def set_work_mode(self, mode: str) -> dict:
        self.orders.append({"order": "work_mode", "mode": mode})
        self.work_mode = mode
        return {"code": 0, "msg": "ok"}

    async def clean_zone(self, rect: list[int]) -> dict:
        mode_reply = None
        if self.work_mode is not None:
            mode_reply = await self.set_work_mode(self.work_mode)
        self.orders.append({"order": "zone", "rect": list(rect)})
        x1, y1, x2, y2 = rect
        edge = [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)]
        self._go([((x1 + x2) / 2, (y1 + y2) / 2), *edge], "cleaning", "idle")
        reply = {"code": 0, "msg": "ok"}
        if mode_reply is None:
            return reply
        return {**reply, "work_mode": {"mode": self.work_mode, **mode_reply}}

    async def stop(self) -> dict:
        self.orders.append({"order": "stop"})
        here = self._position()
        self._route, self._t0 = [here], self.now()
        self._state = self._end_state = "idle"
        return {"code": 0, "msg": "ok"}

    async def dock(self) -> dict:
        self.orders.append({"order": "dock"})
        self._go([self.dock_xy], "returning", "docked")
        return {"code": 0}

    def refresh(self) -> None:
        pass

    def status(self) -> dict:
        if self._state != self._end_state and self._done():
            self._state = self._end_state
        x, y = self._position()
        return {
            "connected": True,
            "state": self._state,
            "battery": self.battery,
            "robot": {"x": round(x), "y": round(y), "a": 0, "age_s": 0.0},
            "dock": {"x": round(self.dock_xy[0]), "y": round(self.dock_xy[1]), "a": 0, "age_s": 0.0},
            "work_mode": self.work_mode,
            "fake": True,
        }
