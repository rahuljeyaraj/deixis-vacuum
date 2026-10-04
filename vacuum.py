"""Send an ECOVACS DEEBOT robot vacuum to clean a box on its map, from your own account.

The vacuum adapter for deixis-doodle (https://github.com/rahuljeyaraj/deixis-doodle). It lives in
its own GPL-3.0 repository because it uses deebot-client (GPL-3.0).

Tested on 3 Oct 2026 with a DEEBOT N50 PRO OMNI (class a2ywac, firmware 1.41.0).

Login details and the saved token live next to this file (gitignored), or in DEIXIS_VACUUM_HOME:
  ecovacs.env        ECOVACS_ACCOUNT=..., ECOVACS_PASSWORD=..., ECOVACS_COUNTRY=IN
  login_state.json   written by `verify`: a fixed device ID and the token (valid 7 days)
Variables already set in the environment win over ecovacs.env.

Usage:
  python vacuum.py verify                  e-mails a code (needed once per device ID / token)
  python vacuum.py verify --code 123456
  python vacuum.py list
  python vacuum.py watch --seconds 60      battery, state, robot and dock positions
  python vacuum.py zone X1 Y1 X2 Y2        clean that box (map millimetres)
  python vacuum.py stop                    stop cleaning (not yet checked on the N50)
  python vacuum.py dock                    go back to the dock
  python vacuum.py sniff --seconds 300     print every message for the robot, incl. the app's orders
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import time

import aiohttp
from deebot_client import events as ev
from deebot_client.api_client import ApiClient
from deebot_client.authentication import Authenticator, create_rest_config
from deebot_client.commands.json.custom import CustomCommand
from deebot_client.device import Device
from deebot_client.models import Credentials
from deebot_client.mqtt_client import MqttClient, create_mqtt_config
from deebot_client.util import md5

HOME = Path(os.environ.get("DEIXIS_VACUUM_HOME") or Path(__file__).resolve().parent)
_STATE = HOME / "login_state.json"


def _load_env_file() -> None:
    """Fill missing ECOVACS_* variables from HOME/ecovacs.env (KEY=value lines)."""
    path = HOME / "ecovacs.env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#") and not os.environ.get(key.strip()):
            os.environ[key.strip()] = value.strip().strip('"')


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        sys.exit(f"Set {name} in {HOME / 'ecovacs.env'} or the environment (see README.md).")
    return v


def _load_state() -> dict:
    return json.loads(_STATE.read_text(encoding="utf-8")) if _STATE.exists() else {}


def _save_state(state: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _save_credentials(c: Credentials) -> None:
    state = _load_state()
    state["credentials"] = {"token": c.token, "user_id": c.user_id, "expires_at": c.expires_at}
    _save_state(state)


def _make_auth(session: aiohttp.ClientSession) -> tuple[Authenticator, str, str]:
    """Authenticator with a stable device ID; reuses saved credentials so no fresh login is needed.

    Since mid-2026 ECOVACS answers a login from an unknown device ID with code 1013 and wants it
    verified by an e-mailed code (`verify`). A fresh login after that can still return 1013
    (DeebotUniverse/client.py#1777), so the credentials from verification are saved and reused.
    """
    country = _env("ECOVACS_COUNTRY").upper()
    state = _load_state()
    if "device_id" not in state:
        state["device_id"] = md5(str(time.time()))
        _save_state(state)
    device_id = state["device_id"]
    rest = create_rest_config(session, device_id=device_id, alpha_2_country=country)
    auth = Authenticator(rest, _env("ECOVACS_ACCOUNT"), md5(_env("ECOVACS_PASSWORD")))
    creds = state.get("credentials")
    if creds and creds["expires_at"] > time.time():
        auth._set_credentials(Credentials(**creds))  # no public setter in deebot-client 18.6

    async def on_credentials(c: Credentials) -> None:
        _save_credentials(c)

    auth.subscribe(on_credentials)
    return auth, device_id, country


async def _devices(session: aiohttp.ClientSession):
    auth, device_id, country = _make_auth(session)
    devices = await ApiClient(auth).get_devices()
    return auth, device_id, country, devices.mqtt, devices.not_supported + devices.xmpp


async def _bot(session: aiohttp.ClientSession, index: int) -> tuple[Device, MqttClient]:
    auth, device_id, country, supported, _ = await _devices(session)
    if not supported:
        sys.exit("No supported robot on this account.")
    bot = Device(supported[index], auth)
    mqtt = MqttClient(create_mqtt_config(device_id=device_id, country=country), auth)
    await bot.initialize(mqtt)
    return bot, mqtt


def zone_command(rect: list[int]) -> CustomCommand:
    """A zone clean as the ECOVACS app sends it: type "freeClean", value "3,null,x1,y1,x2,y2"
    (3 = zone, then two opposite corners in map millimetres). See `cmd_zone`."""
    value = "3,null," + ",".join(str(int(v)) for v in rect)
    return CustomCommand("clean_V2", {"act": "start", "content": {"type": "freeClean", "value": value}})


def stop_command() -> CustomCommand:
    """Stop cleaning. The same order deebot-client's CleanV2(STOP) sends; not yet checked on the
    N50 (capture the app's own stop with `sniff` if the robot refuses it)."""
    return CustomCommand("clean_V2", {"act": "stop", "content": {"type": ""}})


class CloudRobot:
    """One robot on the ECOVACS cloud, kept logged in, for the MCP server (`vacuum_mcp.py`).

    It remembers the last state, battery and the robot's and dock's positions (map mm) from
    the robot's events. Run it on a selector event loop on Windows (see `_run`).
    """

    def __init__(self, index: int = 0, reply_timeout_s: float = 5.0) -> None:
        self.index = index
        self.reply_timeout_s = reply_timeout_s
        self.session: aiohttp.ClientSession | None = None
        self.bot: Device | None = None
        self.mqtt: MqttClient | None = None
        self.state: str | None = None
        self.battery: int | None = None
        self.robot: dict | None = None  # {"x", "y", "a", "t"} (t: time.monotonic())
        self.dock_at: dict | None = None
        self._replies: asyncio.Queue = asyncio.Queue()

    async def start(self) -> None:
        self.session = aiohttp.ClientSession()
        self.bot, self.mqtt = await _bot(self.session, self.index)
        subscribe = self.bot.events.subscribe
        subscribe(ev.PositionsEvent, self._on_positions)
        subscribe(ev.StateEvent, self._on_state)
        subscribe(ev.BatteryEvent, self._on_battery)
        subscribe(ev.CustomCommandEvent, self._on_reply)

    async def close(self) -> None:
        if self.mqtt is not None:
            await self.mqtt.disconnect()
        if self.session is not None:
            await self.session.close()

    async def _on_positions(self, event: ev.PositionsEvent) -> None:
        for p in event.positions:
            at = {"x": p.x, "y": p.y, "a": p.a, "t": time.monotonic()}
            if p.type.name == "DEEBOT":
                self.robot = at
            elif p.type.name == "CHARGER":
                self.dock_at = at

    async def _on_state(self, event: ev.StateEvent) -> None:
        self.state = event.state.name.lower()

    async def _on_battery(self, event: ev.BatteryEvent) -> None:
        self.battery = event.value

    async def _on_reply(self, event: ev.CustomCommandEvent) -> None:
        await self._replies.put(event.response)

    async def _send(self, command: CustomCommand) -> dict:
        """Send one order and wait for the robot's reply body ({"code": 0, ...} is accepted)."""
        assert self.bot is not None, "start() first"
        while not self._replies.empty():
            self._replies.get_nowait()
        await self.bot.execute_command(command)
        try:
            reply = await asyncio.wait_for(self._replies.get(), self.reply_timeout_s)
        except TimeoutError:
            return {"code": None, "msg": f"no reply within {self.reply_timeout_s} s"}
        return reply.get("body", {}) if isinstance(reply, dict) else {}

    async def clean_zone(self, rect: list[int]) -> dict:
        return await self._send(zone_command(rect))

    async def stop(self) -> dict:
        return await self._send(stop_command())

    async def dock(self) -> dict:
        assert self.bot is not None, "start() first"
        await self.bot.execute_command(self.bot.capabilities.charge.execute())
        return {"code": 0}

    def refresh(self) -> None:
        """Ask the robot for fresh positions (they arrive as events)."""
        if self.bot is not None:
            self.bot.events.request_refresh(ev.PositionsEvent)

    def status(self) -> dict:
        now = time.monotonic()

        def pos(p: dict | None) -> dict | None:
            if p is None:
                return None
            return {"x": p["x"], "y": p["y"], "a": p["a"], "age_s": round(now - p["t"], 2)}

        return {
            "connected": self.bot is not None,
            "state": self.state,
            "battery": self.battery,
            "robot": pos(self.robot),
            "dock": pos(self.dock_at),
        }


def _now() -> str:
    return time.strftime("%H:%M:%S")


def _print_events(bot: Device, seen: dict[str, int]) -> None:
    def printer(name: str):
        async def on_event(event) -> None:
            seen[name] = seen.get(name, 0) + 1
            print(f"{_now()} {name}: {event}", flush=True)

        return on_event

    for cls in (ev.BatteryEvent, ev.StateEvent, ev.PositionsEvent, ev.ErrorEvent):
        bot.events.subscribe(cls, printer(cls.__name__))


async def cmd_verify(code: str | None) -> int:
    async with aiohttp.ClientSession() as s:
        auth, _, _ = _make_auth(s)
        if not code:
            await auth.request_device_verification_code()
            print("ECOVACS has e-mailed a code. Run: python vacuum.py verify --code <the code>")
            return 0
        c = await auth.verify_device(code)
        _save_credentials(c)
        print(f"Verified. Login saved until {time.ctime(c.expires_at)}.")
        await auth.teardown()
    return 0


async def cmd_list() -> int:
    async with aiohttp.ClientSession() as s:
        _, _, _, supported, other = await _devices(s)
        print(f"{len(supported)} supported robot(s):")
        for i, d in enumerate(supported):
            api = d.api
            print(f"  [{i}] {api.get('nick')}: {api.get('deviceName')}, class {api['class']}")
        for d in other:
            print(f"  not supported: {d.get('nick')}: {d.get('deviceName')}, class {d.get('class')}")
    return 0


async def cmd_watch(seconds: int, index: int) -> int:
    async with aiohttp.ClientSession() as s:
        bot, mqtt = await _bot(s, index)
        seen: dict[str, int] = {}
        _print_events(bot, seen)
        print(f"Watching for {seconds} s.")
        await asyncio.sleep(seconds)
        print(f"\nEvent counts: {seen}")
        await mqtt.disconnect()
    return 0


async def cmd_zone(rect: list[int], index: int, watch: int) -> int:
    """Clean a box, sent the way the ECOVACS Android app sends a zone clean.

    deebot-client's own area clean (clean_V2 type "customArea") is refused by this firmware with
    code 20011 "unknown type". The app, captured with `sniff`, sends type "freeClean" with value
    "3,null,x1,y1,x2,y2": 3 = zone, then two opposite corners in map millimetres.
    """
    async with aiohttp.ClientSession() as s:
        bot, mqtt = await _bot(s, index)
        replies: list = []

        async def on_reply(event) -> None:
            replies.append(event.response)

        bot.events.subscribe(ev.CustomCommandEvent, on_reply)
        await bot.execute_command(zone_command(rect))
        await asyncio.sleep(2)
        body = replies[0].get("body", {}) if replies else {}
        print(f"{_now()} zone {rect} sent; robot replied {body}")
        if body.get("code") != 0:
            await mqtt.disconnect()
            return 1
        print("The dock may prepare the mop first; leaving took about 2.5 min on the N50.")
        if watch:
            _print_events(bot, {})
            await asyncio.sleep(watch)
        await mqtt.disconnect()
    return 0


async def cmd_stop(index: int) -> int:
    async with aiohttp.ClientSession() as s:
        bot, mqtt = await _bot(s, index)
        replies: list = []

        async def on_reply(event) -> None:
            replies.append(event.response)

        bot.events.subscribe(ev.CustomCommandEvent, on_reply)
        await bot.execute_command(stop_command())
        await asyncio.sleep(2)
        body = replies[0].get("body", {}) if replies else {}
        print(f"{_now()} stop sent; robot replied {body}")
        await mqtt.disconnect()
    return 0 if body.get("code") == 0 else 1


async def cmd_dock(index: int) -> int:
    async with aiohttp.ClientSession() as s:
        bot, mqtt = await _bot(s, index)
        await bot.execute_command(bot.capabilities.charge.execute())
        print("return-to-dock sent")
        await asyncio.sleep(3)
        await mqtt.disconnect()
    return 0


class _PrintMessages(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if msg.startswith("Got message"):
            print(_now(), msg[:1500], flush=True)


async def cmd_sniff(seconds: int, index: int) -> int:
    """Print every MQTT message for the robot. The library also subscribes to orders sent *to* the
    robot (iot/p2p/.../q/...), so this shows exactly what the official app sends."""
    log = logging.getLogger("deebot_client.mqtt_client")
    log.setLevel(logging.DEBUG)
    log.addHandler(_PrintMessages())
    async with aiohttp.ClientSession() as s:
        _, mqtt = await _bot(s, index)
        print(f"Listening for {seconds} s.", flush=True)
        await asyncio.sleep(seconds)
        await mqtt.disconnect()
    return 0


def _run(coro) -> int:
    """MQTT (aiomqtt) needs add_reader/add_writer, which Windows' default Proactor loop lacks."""
    if sys.platform == "win32":
        return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(coro)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--device", type=int, default=0, help="index from `list` (default 0)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--code", help="the code from the e-mail; leave out to have a code sent")
    sub.add_parser("list")
    w = sub.add_parser("watch")
    w.add_argument("--seconds", type=int, default=60)
    z = sub.add_parser("zone")
    z.add_argument("rect", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    z.add_argument("--watch", type=int, default=0, help="seconds to print events after sending")
    sub.add_parser("stop")
    sub.add_parser("dock")
    n = sub.add_parser("sniff")
    n.add_argument("--seconds", type=int, default=300)
    a = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("deebot_client.api_client").setLevel(logging.ERROR)  # unsupported-robot noise
    _load_env_file()
    match a.cmd:
        case "verify":
            return _run(cmd_verify(a.code))
        case "list":
            return _run(cmd_list())
        case "watch":
            return _run(cmd_watch(a.seconds, a.device))
        case "zone":
            return _run(cmd_zone(a.rect, a.device, a.watch))
        case "stop":
            return _run(cmd_stop(a.device))
        case "dock":
            return _run(cmd_dock(a.device))
        case "sniff":
            return _run(cmd_sniff(a.seconds, a.device))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
