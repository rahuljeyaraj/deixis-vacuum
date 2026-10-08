"""The MCP server with the pretend robot: what a client (the vacuum app) sees."""

import asyncio
import sys
from pathlib import Path

from deebot_client.commands.json.custom import CustomCommand
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from fake_robot import FakeRobot
from vacuum import WORK_MODES, work_mode_command
from vacuum_mcp import zone_from_where

ROOT = Path(__file__).resolve().parents[1]


def box(x, y, w, h, frame="map", kind="box"):
    return {"version": "1.0", "frame": frame, "kind": kind, "box_mm": [x, y, w, h],
            "mean_mm": [x + w / 2, y + h / 2], "source": "gesture"}  # fmt: skip


def call_server(*calls, args=("--fake", "--fake-speed", "1e9")):
    """Start the server, make the calls [(tool, args)], return their structured results."""

    async def go():
        params = StdioServerParameters(
            command=sys.executable, args=[str(ROOT / "vacuum_mcp.py"), *args], cwd=str(ROOT)
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                tools = [t.name for t in (await s.list_tools()).tools]
                out = []
                for name, a in calls:
                    r = await s.call_tool(name, a)
                    data = r.structured_content
                    out.append(data.get("result", data) if set(data) == {"result"} else data)
                return tools, out

    return asyncio.run(go())


def test_a_box_on_the_map_is_cleaned_exactly():
    tools, (res, st) = call_server(
        ("clean_area", {"where": box(1850.4, -950.2, 500, 499.6)}), ("status", {})
    )
    assert set(tools) == {"clean_area", "stop", "dock", "status", "locate"}
    assert res["ok"] and res["zone"] == [1850, -950, 2350, -451]
    assert res["reply"]["work_mode"]["mode"] == "vacuum"  # no mop: no wait at the dock
    assert st["work_mode"] == "vacuum"
    assert st["fake"] and st["robot"] is not None and st["dock"] == {"x": 24, "y": 287, "a": 0,
                                                                     "age_s": 0.0}  # fmt: skip


def test_a_box_it_cannot_use_is_refused_not_changed():
    _, out = call_server(
        ("clean_area", {"where": box(0, 0, 1000, 1000, frame="floor")}),
        ("clean_area", {"where": {**box(0, 0, 1000, 1000), "kind": "point"}}),
        ("clean_area", {"where": box(0, 0, 9000, 1000)}),
        ("clean_area", {"where": box(0, 0, 100, 1000)}),
    )
    assert [r["ok"] for r in out] == [False] * 4
    assert "frame 'floor'" in out[0]["why"] and "not a 'point'" in out[1]["why"]
    assert "width is 9000 mm" in out[2]["why"] and "width is 100 mm" in out[3]["why"]


def test_stop_and_dock():
    _, (clean, stop, dock, st) = call_server(
        ("clean_area", {"where": box(1000, 1000, 1000, 1000)}),
        ("stop", {}),
        ("dock", {}),
        ("locate", {"wait_s": 0}),
    )
    assert clean["ok"] and stop["ok"] and dock["ok"]
    assert st["state"] == "docked" and (st["robot"]["x"], st["robot"]["y"]) == (24, 287)


def test_zone_corners_are_whole_millimetres():
    rect, why = zone_from_where(box(10.5, 20.49, 300, 400), "map", 200, 8000)
    assert rect == [10, 20, 310, 420] and why == ""


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_the_fake_robot_drives_to_the_zone_and_round_it():
    clock = Clock()
    r = FakeRobot(dock=(0, 0), speed_mm_s=100, leave_s=5, now=clock)
    assert r.status()["state"] == "docked"
    asyncio.run(r.clean_zone([1000, 0, 2000, 1000]))
    clock.t = 4.0  # still preparing on the dock
    assert (r.status()["robot"]["x"], r.status()["state"]) == (0, "cleaning")
    clock.t = 5.0 + 15.0  # 1500 mm along: at the zone's centre (1500, 500) after 1581 mm
    s = r.status()
    assert s["state"] == "cleaning" and 1300 < s["robot"]["x"] < 1500
    clock.t = 1000
    assert r.status()["state"] == "idle"
    asyncio.run(r.stop())
    asyncio.run(r.dock())
    clock.t = 2000
    s = r.status()
    assert s["state"] == "docked" and (s["robot"]["x"], s["robot"]["y"]) == (0, 0)
    assert [o["order"] for o in r.orders] == ["work_mode", "zone", "stop", "dock"]
    assert r.orders[0]["mode"] == "vacuum"


def test_the_work_mode_order_is_the_app_s_vacuum_only_choice():
    order = work_mode_command("vacuum")
    assert order == CustomCommand("setWorkMode", {"mode": 1}) and order.NAME == "setWorkMode"
    assert WORK_MODES["vacuum_and_mop"] == 0  # deebot_client.events.WorkMode
    try:
        work_mode_command("dust")
    except ValueError as e:
        assert "vacuum" in str(e)
    else:
        raise AssertionError("an unknown work mode must be refused")


def test_the_robot_s_own_mode_is_left_alone_when_asked():
    r = FakeRobot(work_mode=None)
    asyncio.run(r.clean_zone([0, 0, 500, 500]))
    assert [o["order"] for o in r.orders] == ["zone"]
