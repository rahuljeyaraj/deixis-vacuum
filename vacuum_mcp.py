"""The robot vacuum as an MCP server: give it a place and it cleans there.

    deixis-vacuum-mcp            the robot on your ECOVACS account (login: see README.md)
    deixis-vacuum-mcp --fake     a pretend robot: nothing moves (tests, dry runs)

The place is a `Where` box, the JSON message of the deixis core
(https://github.com/rahuljeyaraj/deixis), already in the robot's own map frame (map mm).
This server knows nothing else about deixis: it reads `frame`, `kind` and `box_mm` and sends
a zone clean of exactly that box. A box it can't use is refused with the reason, never moved
or shrunk to fit.

Tools:
- clean_area(where): clean that box (a zone clean, as the ECOVACS app sends one).
- stop(): stop cleaning now.
- dock(): go back to the dock.
- status(): state, battery, and the robot's and the dock's positions in map mm (with their
  age in seconds).
- locate(): ask the robot for fresh positions, wait a moment, then the same as status()
  (the app uses it to fit the floor camera to the map).

Logs go to stderr; stdout is the MCP channel.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from mcp.server.mcpserver import MCPServer

log = logging.getLogger("deixis-vacuum")


def zone_from_where(
    where: dict[str, Any], frame: str, min_side_mm: float, max_side_mm: float
) -> tuple[list[int] | None, str]:
    """The zone's corners [x1, y1, x2, y2] in whole map mm, or None and why not."""
    if not isinstance(where, dict):
        return None, "where must be a Where object"
    if where.get("frame") != frame:
        return None, f"the box is in frame {where.get('frame')!r}; this robot needs {frame!r}"
    if where.get("kind") != "box" or where.get("box_mm") is None:
        return None, f"this robot cleans boxes, not a {where.get('kind')!r}"
    try:
        x, y, w, h = (float(v) for v in where["box_mm"])
    except (TypeError, ValueError):
        return None, "box_mm must be [x, y, width, height]"
    for name, side in (("width", w), ("height", h)):
        if not min_side_mm <= side <= max_side_mm:
            return None, (
                f"the box's {name} is {side:.0f} mm; this robot takes "
                f"{min_side_mm:.0f} to {max_side_mm:.0f} mm"
            )
    return [round(x), round(y), round(x + w), round(y + h)], ""


def build_server(
    robot: Any, frame: str = "map", min_side_mm: float = 200.0, max_side_mm: float = 8000.0
) -> MCPServer:
    mcp = MCPServer(
        "deixis-vacuum",
        version="0.2.0",
        instructions=(
            "A robot vacuum. clean_area takes a deixis Where box in the robot's map frame "
            "(map mm) and cleans exactly that box; stop, dock, status and locate do what "
            "they say."
        ),
    )

    @mcp.tool()
    async def clean_area(where: dict[str, Any]) -> dict[str, Any]:
        """Clean this box: a deixis Where with frame 'map', kind 'box' and box_mm
        [x, y, width, height] in map mm."""
        rect, why = zone_from_where(where, frame, min_side_mm, max_side_mm)
        if rect is None:
            log.warning("clean_area refused: %s", why)
            return {"ok": False, "why": why}
        reply = await robot.clean_zone(rect)
        ok = reply.get("code") == 0
        log.info("zone %s sent; robot replied %s", rect, reply)
        return {"ok": ok, "zone": rect, "reply": reply, **({} if ok else {"why": str(reply)})}

    @mcp.tool()
    async def stop() -> dict[str, Any]:
        """Stop cleaning now."""
        reply = await robot.stop()
        log.info("stop sent; robot replied %s", reply)
        return {"ok": reply.get("code") == 0, "reply": reply}

    @mcp.tool()
    async def dock() -> dict[str, Any]:
        """Go back to the dock."""
        reply = await robot.dock()
        return {"ok": reply.get("code") == 0, "reply": reply}

    @mcp.tool()
    async def status() -> dict[str, Any]:
        """State, battery, and the robot's and the dock's positions (map mm, age in s)."""
        return robot.status()

    @mcp.tool()
    async def locate(wait_s: float = 2.0) -> dict[str, Any]:
        """Ask for fresh positions, wait up to wait_s seconds, then report status."""
        robot.refresh()
        await asyncio.sleep(max(0.0, min(wait_s, 10.0)))
        return robot.status()

    return mcp


async def serve(robot: Any, **kw: Any) -> None:
    await robot.start()
    try:
        await build_server(robot, **kw).run_stdio_async()
    finally:
        await robot.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="deixis-vacuum-mcp", description=__doc__.splitlines()[0])
    ap.add_argument("--fake", action="store_true", help="a pretend robot (nothing moves)")
    ap.add_argument("--device", type=int, default=0, help="index from `vacuum.py list`")
    ap.add_argument("--frame", default="map", help="the frame name boxes must be in")
    ap.add_argument("--min-side", type=float, default=200.0, help="smallest box side, mm")
    ap.add_argument("--max-side", type=float, default=8000.0, help="largest box side, mm")
    ap.add_argument("--fake-speed", type=float, default=300.0, help="--fake: mm per second")
    ap.add_argument("--fake-leave", type=float, default=0.0, help="--fake: s on the dock first")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(name)s: %(message)s")
    if a.fake:
        from fake_robot import FakeRobot

        robot: Any = FakeRobot(speed_mm_s=a.fake_speed, leave_s=a.fake_leave)
    else:
        import vacuum

        logging.getLogger("deebot_client.api_client").setLevel(logging.ERROR)
        vacuum._load_env_file()
        robot = vacuum.CloudRobot(a.device)
    coro = serve(robot, frame=a.frame, min_side_mm=a.min_side, max_side_mm=a.max_side)
    if sys.platform == "win32":  # MQTT needs add_reader, which the Proactor loop lacks
        asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(coro)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
