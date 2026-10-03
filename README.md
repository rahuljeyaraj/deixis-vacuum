# deixis-vacuum

Send a robot vacuum to clean a box on its map from Python: "clean **here**, this big".

This is the vacuum adapter for [deixis-doodle](https://github.com/rahuljeyaraj/deixis-doodle), which turns pointing and speech into a place (`Where`) that any robot can use. deixis-doodle drives a pen plotter, and this repo drives an unmodified, off-the-shelf robot vacuum. It's a separate repo because it uses [deebot-client](https://github.com/DeebotUniverse/client.py), which is GPL-3.0. deixis-doodle stays MIT.

## Result (3 Oct 2026)

| Robot | Class | Result |
|---|---|---|
| DEEBOT N50 PRO OMNI (firmware 1.41.0) | `a2ywac` | **Works.** Login, battery, state, live robot and dock positions, zone clean, return to dock. |
| DEEBOT Y1 PRO | `cqyi87` | Not supported by deebot-client. |

The test: `zone 1850 -950 2350 -450` (a 50 cm box, in map millimetres) was sent at 20:44:43. The robot left the dock about 2.5 minutes later, and its reported positions traced that box: x 1859 to 2353, y −902 to −451. Then it returned to its dock by itself.

## What we learned

- **Login now needs an e-mailed code.** Since mid-2026, ECOVACS answers a login from an unknown device ID with code 1013 ("Please update to the latest version"). `verify` asks for a code and approves a fixed device ID. A fresh password login after that can still fail ([client.py#1777](https://github.com/DeebotUniverse/client.py/issues/1777)), so the token from verification (valid 7 days) is saved and reused.
- **The library's area clean is refused.** On this firmware, `clean_V2` with type `customArea` returns `20011 unknown type`. The type `spot` is accepted but does nothing.
- **What works is the app's own zone order**, captured with `sniff`:

  ```json
  {"act": "start", "content": {"type": "freeClean", "value": "3,null,X1,Y1,X2,Y2"}}
  ```

  `3` means zone. The four numbers are two opposite corners in map millimetres. Others report that room cleans use the same type with value `1,<room id>` (not tested here).
- **Windows:** deebot-client 18.x has Windows wheels for Python 3.14 only, and its MQTT client needs the selector event loop (see `_run`).

## Set-up

```powershell
uv venv --python 3.14 .venv
uv pip install --python .venv\Scripts\python.exe -e .
```

Put your ECOVACS app login in `~/.deixis-vacuum/ecovacs.env`. Use `ecovacs.env.example` as the template. Never put it in this repo. To use another folder, set `DEIXIS_VACUUM_HOME`.

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
| `zone X1 Y1 X2 Y2 [--watch 120]` | Cleans that box |
| `dock` | Sends the robot back to its dock |
| `sniff --seconds 300` | Prints every message for the robot, including the orders the official app sends. Use it to learn a new robot's commands. |

`--device N` (before the command) picks a robot from `list`. The default is 0.

To find a box: run `watch`, or run `sniff` while you draw a zone in the app.

## Safety and rules

- Clear the floor and stay near the robot. The app's stop button always works.
- This uses the same cloud API as the app, with your own account and your own robot. Nothing changes the robot's firmware or settings.
- ECOVACS doesn't officially support third-party control. Treat this as a demo, not something to depend on.
- ECOVACS and DEEBOT are trademarks of ECOVACS Robotics. This project isn't affiliated with or endorsed by them.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
