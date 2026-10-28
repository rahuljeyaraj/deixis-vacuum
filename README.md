# deixis-vacuum

Send a robot vacuum to clean a box on its map from Python or over MCP: "clean **here**".

This is the robot vacuum's driver for [deixis](https://github.com/rahuljeyaraj/deixis), the core that turns pointing and speech into a place (`Where`) that any robot can use. The vacuum app in [deixis-doodle](https://github.com/rahuljeyaraj/deixis-doodle) ("clean here") sends that place here, and this repo drives an unmodified, off-the-shelf robot vacuum. It's a separate repo because it uses [deebot-client](https://github.com/DeebotUniverse/client.py), which is GPL-3.0. deixis and deixis-doodle stay MIT. All of them are in one clone at [deixis-robots](https://github.com/rahuljeyaraj/deixis-robots).

It needs Python 3.14 (see Set-up), unlike the other repos.

## Result (3 Oct 2026)

| Robot | Class | Result |
|---|---|---|
| DEEBOT N50 PRO OMNI (firmware 1.41.0) | `a2ywac` | **Works.** Login, battery, state, live robot and dock positions, zone clean, return to dock. |
| DEEBOT Y1 PRO | `cqyi87` | Not supported by deebot-client. |

The test: `zone 1850 -950 2350 -450` (a 50 cm box, in map millimetres) was sent at 20:44:43. The robot left the dock about 2.5 minutes later, and its reported positions traced that box: x 1859 to 2353, y −902 to −451. Then it returned to its dock by itself.

Those 2.5 minutes were the dock wetting the mop. Since 8 Oct a clean is sent as vacuum only, which skips that preparation and leaves the floor dry. The new start has not been timed on the robot yet.

## What we learned

- **Login now needs an e-mailed code.** Since mid-2026, ECOVACS answers a login from an unknown device ID with code 1013 ("Please update to the latest version"). `verify` asks for a code and approves a fixed device ID. A fresh password login after that can still fail ([client.py#1777](https://github.com/DeebotUniverse/client.py/issues/1777)), so the token from verification (valid 7 days) is saved and reused.
- **The library's area clean is refused.** On this firmware, `clean_V2` with type `customArea` returns `20011 unknown type`. The type `spot` is accepted but does nothing.
- **What works is the app's own zone order**, captured with `sniff`:

  ```json
  {"act": "start", "content": {"type": "freeClean", "value": "3,null,X1,Y1,X2,Y2"}}
  ```

  `3` means zone. The four numbers are two opposite corners in map millimetres. Others report that room cleans use the same type with value `1,<room id>` (not tested here).
- **The mop is what makes it slow to start.** With the mop fitted, the OMNI dock wets it and washes it again on the way back, and the robot took about 2.5 minutes to leave the dock. Sending `setWorkMode` with `{"mode": 1}` (vacuum only) before the zone order skips all of that, and leaves the floor dry. The modes are 0 vacuum and mop, 1 vacuum, 2 mop, 3 mop after vacuum. `zone` sets vacuum only by default; `--mode keep` leaves whatever the robot is on.
- **Windows:** deebot-client 18.x has Windows wheels for Python 3.14 only, and its MQTT client needs the selector event loop (see `_run`).

## Set-up

```powershell
uv venv --python 3.14 .venv
uv pip install --python .venv\Scripts\python.exe -e .
```

Copy `ecovacs.env.example` to `ecovacs.env` in this folder and fill in your ECOVACS app login. `verify` saves the token to `login_state.json` here too. Git ignores both files, so they're never committed. To keep them in another folder, set `DEIXIS_VACUUM_HOME`.

```powershell
.venv\Scripts\python.exe vacuum.py verify                 # ECOVACS e-mails you a code
.venv\Scripts\python.exe vacuum.py verify --code 123456
.venv\Scripts\python.exe vacuum.py list
```

## Commands

| Command | What it does |
|---|---|
| `list` | Robots on the account, with class codes |
| `watch --seconds 60` | Prints battery, state, errors, and robot and dock positions (map mm) |
| `zone X1 Y1 X2 Y2 [--watch 120]` | Cleans that box, **vacuum only** (no mop, so no wait at the dock). `--mode vacuum_and_mop\|mop\|mop_after_vacuum` picks another mode, `--mode keep` leaves the robot's own |
| `mode vacuum` | Sets the work mode and leaves it there (the same choice as in the app) |
| `stop` | Stops cleaning. Sends the same order as deebot-client's own stop. **Not yet tried on the N50.** If the robot refuses it, run `sniff` while you press stop in the app |
| `dock` | Sends the robot back to its dock |
| `sniff --seconds 300` | Prints every message for the robot, including the orders the official app sends. Use it to learn a new robot's commands. |

`--device N` (before the command) picks a robot from `list`. The default is 0.

To find a box: run `watch`, or run `sniff` while you draw a zone in the app.

## As an MCP server

`deixis-vacuum-mcp` lets an app talk to the robot over MCP (stdio). The vacuum app in deixis-doodle uses it. It stays logged in and offers five tools:

| Tool | What it does |
|---|---|
| `clean_area(where)` | Cleans one box, vacuum only (`--work-mode` changes that; `keep` leaves the robot's own). `where` is a deixis `Where` box in the robot's map frame: `{"frame": "map", "kind": "box", "box_mm": [x, y, width, height], ...}`. The box is cleaned exactly as given. A box in another frame, or with a side under 200 mm or over 8 m, is refused with the reason. It's never moved or shrunk to fit |
| `stop()` | Stops cleaning |
| `dock()` | Sends the robot back to its dock |
| `status()` | State, battery, and the robot's and dock's positions in map mm, with how old each is |
| `locate(wait_s)` | Asks the robot for fresh positions, waits, then returns `status()`. Used to line the floor camera up with the map |

```powershell
.venv\Scripts\deixis-vacuum-mcp.exe             # your robot (log in first, as above)
.venv\Scripts\deixis-vacuum-mcp.exe --fake      # a pretend robot: no login, nothing moves
```

This server doesn't import deixis. It only reads the `Where` JSON, so any app that can send that message can drive the robot.

Tests (pretend robot only, no login):

```powershell
uv pip install --python .venv\Scripts\python.exe -e ".[test]"
.venv\Scripts\python.exe -m pytest
```

## Safety and rules

- Clear the floor and stay near the robot. The app's stop button always works.
- This uses the same cloud API as the app, with your own account and your own robot. Nothing changes the robot's firmware. The one setting it does change is the work mode, which it sets to vacuum only before a clean, exactly as the app's own Vacuum / Mop choice does; the robot keeps that mode afterwards.
- ECOVACS doesn't officially support third-party control. Treat this as a demo, not something to depend on.
- ECOVACS and DEEBOT are trademarks of ECOVACS Robotics. This project isn't affiliated with or endorsed by them.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
