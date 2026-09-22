# ESP32 "Impossible to Ignore" Alarm Clock — Active Plan

## Context

The original plan (see `README.md`) was a Fairphone 2 pinned as a
single-app proximity clock. That's superseded: the FP2's USB data port
turned out to be faulty, and a Samsung GT-S7390 stand-in was ruled out
entirely (Android 4.1.2 is below Flutter's and Screen Pinning's minimum
API 21). The project pivoted to dedicated ESP32 hardware as the actual
alarm device.

The control layer was then redesigned twice more:
- Dropped Flutter + Bluetooth in favor of **the ESP32 hosting its own
  web server** (a small Vue.js frontend + REST API), so the phone needs
  no app install — just a browser, on the same WiFi. This also drops the
  phone-side toolchain entirely (no adb/BLE plugin needed).
- Dropped Arduino-ESP32/PlatformIO/C++ in favor of **MicroPython +
  microdot** for the firmware — no compile step, edit-and-push iteration
  over serial, faster feedback loop while learning. Tradeoff accepted:
  less RAM headroom than C++, and the `EspTuya` C++ library found for
  bulb control won't plug in directly (a MicroPython-side reimplementation
  of the local Tuya protocol would be needed later, when bulbs are
  revisited).

Core design goal (unchanged): an alarm that's impossible to sleep through
or dismiss accidentally. Sound starts quiet and ramps up in intensity;
WiFi smart bulbs fade in with soft colors; dismissing it requires holding
a physical button for a continuous 2 minutes; and the schedule can't be
edited from the app in the hour before it's due to fire.

The ESP32 is already wired up with a battery, a switch, and a buzzer.
Bulb integration is **deprioritized** — first priority is proving the
ESP32 and a phone browser can talk to each other end to end (Phase A),
then building out the full alarm behavior (Phase B).

## Architecture at a glance

- **ESP32 = source of truth and the actual alarm hardware.** Runs
  standalone — the phone is a thin remote control, not required for the
  alarm to fire.
- **Firmware: MicroPython**, files pushed over serial with `mpremote`
  (no compile/flash cycle — edit a `.py` file, push it, it runs).
- **Web server: [`microdot`](https://github.com/miguelgrinberg/microdot)**
  — a lightweight Python web framework built for microcontrollers (same
  author as Flask). Serves:
  - The built Vue static files, from the ESP32's onboard flash
    filesystem.
  - A small REST API (`GET /api/status`, `POST /api/alarm`, etc.).
- **WiFi mode: Station (STA)**, joining the home network — not a
  standalone Access Point. Both NTP sync and the eventual LocalTuya bulb
  link need the ESP32 on the home LAN with internet, so this is built in
  from the start. Credentials hardcoded for now.
- **Fixed local IP**, not mDNS — `.local` hostname resolution is
  inconsistent on mobile browsers (especially Android Chrome), so a
  static IP is the reliable choice for something opened every night.
- **Frontend**: Vue 3 + Vite, built to a small static bundle and copied
  onto the ESP32's filesystem via `mpremote`. No native app, no app
  store — a URL you open (bookmarkable / "Add to Home Screen").
- **Persistent state** lives on the ESP32 as a small JSON file on its
  filesystem (MicroPython has no direct NVS/Preferences equivalent, but a
  JSON config file serves the same "the alarm has its own memory" role).

## Bulb control protocol — resolved direction: LocalTuya, not flashing

Bulb identified: **Lepro / LE lampUX, model PR901001-EU-a** — confirmed
Tuya-based (pairs via the Smart Life/Tuya Smart app). Deprioritized for
now; captured here so the research isn't lost.

- A Tasmota template exists for this exact model, but
  [Blakadder's template notes](https://templates.blakadder.com/le_901001-EU-a.html)
  warn that newer production runs of this bulb shipped with a WiFi module
  that `tuya-convert` can't flash. Since the manufacture batch of this
  specific unit is unknown, betting on flashability risks bricking it.
- **Chosen approach: LocalTuya**, not flashing. Extract the bulb's local
  encryption key once via a free Tuya IoT developer account + linking the
  Smart Life app (standard, well-documented, one-time, needs internet).
  After that, the ESP32 talks to the bulb directly over the LAN using its
  existing stock firmware and local key — no flashing, no brick risk, and
  zero cloud dependency at runtime once the key is extracted.
- The [`EspTuya`](https://github.com/FrBerger83/EspTuya) library found
  earlier is C++/Arduino-only, so it doesn't carry over to MicroPython
  directly. When bulb work resumes: either port the relevant parts of its
  local-protocol logic to MicroPython, or implement the local Tuya
  protocol (3.4/3.5, AES-ECB-encrypted JSON payloads) directly — it's a
  documented (community-reverse-engineered) protocol, `tinytuya`'s source
  is the best reference. Re-verify current options at that point.

## Where we left off (pick up here tomorrow)

Last action taken: pushed a rewritten `esp32/webapp.py` that drops
`microdot`/`asyncio` entirely in favor of a plain synchronous `socket`
server (see "Dropped microdot's asyncio-based server" note further down)
— **this has not been tested yet**. The push/reset commands were given
but no boot log was pasted back before the session ended.

**First thing tomorrow**: run
```
mpremote connect /dev/ttyUSB0 cp esp32/webapp.py :webapp.py
mpremote connect /dev/ttyUSB0 reset
```
and check the serial log for `listening on ...` with no crash, then test
`curl http://<ip>/api/ping` and the hardware test page from a phone
(switch/LED/buzzer buttons — see step 8 below for wiring). If this
synchronous-socket approach works cleanly, Phase A's remaining blocker
(the `OSError: -203` crash loop from `microdot`'s async server) is fully
resolved.

## Phase A — Prove ESP32 ↔ phone web page connectivity (current focus)

1. ✅ Confirmed board: **ESP32-WROOM-32**. Flashed MicroPython via
   `esptool` (firmware: `ESP32_GENERIC-20260824-v1.29.0.bin`, kept in
   repo root).
2. ✅ `mpremote` set up (in `~/.mpy-venv`), used for pushing files/REPL
   access over serial (`/dev/ttyUSB0`, a CH340 USB-serial adapter).
3. ✅ Minimal firmware written: `esp32/main.py` + `esp32/microdot.py`
   (single-file `microdot` library, copied straight from upstream).
   Connects to home WiFi (STA, hardcoded credentials — SSID must be the
   **2.4GHz** network; this box's 5GHz variant has a `-11ac` suffix and
   is invisible to the ESP32's scan). Currently uses **DHCP**, not a
   static IP yet — see step 7 below.
4. ⬜ Real Vue app not built yet — `main.py` currently serves a small
   **inline HTML test page** at `/` (button that calls `/api/ping`),
   good enough to prove connectivity but due to be replaced (step 8).
5. ✅ **Verified from an actual phone browser** on the same WiFi:
   `http://<esp32-ip>/` renders the test page, "Ping ESP32" button
   round-trips `/api/ping` successfully (`{"status":"ok","uptime_ms":...}`).
   **Phase A's core connectivity goal is proven.**
6. ⬜ Wire-check the switch/buzzer — not done yet.

### Remaining before Phase A is fully closed out

7. ⬜ **Static IP — reverted to plain DHCP.** Tried pinning
   `192.168.1.6` client-side via `wlan.ifconfig((ip, mask, gateway, dns))`
   before `wlan.connect()`; this caused the ESP32 to print "connected"
   and the IP, but then be unreachable (`curl` → "No route to host",
   `ping` intermittently failing) even at strong signal (`rssi: -44`)
   right next to the router. Likely an IP conflict or the router's
   DHCP-snooping/ARP security rejecting a client that skipped its DHCP
   handshake. `main.py` now prints the ESP32's WiFi MAC address at boot
   (`ubinascii.hexlify(wlan.config("mac"), ":")`) — **still need to**: (a)
   grab that MAC from the serial log, (b) add a **DHCP reservation** for
   it in the router admin UI (pin `192.168.1.6` or any fixed address
   server-side), which keeps the normal DHCP handshake intact instead of
   the device self-assigning an IP.
8. ⬜ **Wire-check switch + buzzer + LED independently of app logic** —
   firmware side is done (`main.py` now drives real GPIOs, see below);
   still needs the user to actually wire it up and flash/test.
   - Switch: GPIO4 (internal pull-up, other leg to GND)
   - LED: GPIO26 (through a 220–330Ω resistor to GND)
   - Buzzer: GPIO27, driven via `machine.PWM` (assumes a passive buzzer,
     either a 3-pin driver module or a bare piezo through an NPN
     transistor — needed since Phase B calls for frequency/intensity
     ramping, which an active buzzer can't do)
   - Test page at `/` now uses **Vue 3 (via CDN script, no build step)**
     with a switch-state poller and LED/buzzer buttons — this is a
     hardware smoke test, not the final app.
9. ⬜ **Replace the hardware-test page with the real Vue 3 + Vite app**:
   scaffold a proper Vite project (build step, not just a CDN `<script>`
   tag), build it, push the static bundle onto the ESP32's filesystem via
   `mpremote`, and swap `main.py`'s `/` route to serve it instead of the
   inline `INDEX_HTML` string.

Once 8–9 are done, Phase A is fully closed and Phase B (real alarm
behavior) starts.

## Phase B — Full alarm behavior (outline, to detail once Phase A works)

- **ESP32**: JSON-persisted alarm schedule; NTP sync over WiFi
  (`ntptime` module) for real time (internal clock drift over one night
  is negligible — no extra RTC hardware needed); a wake state machine
  driving a gradual bulb color/brightness ramp-in alongside a buzzer
  intensity/frequency ramp (`machine.PWM` duty/frequency stepping);
  dismiss logic requiring a continuous 2-minute button hold (debounced,
  resets on release); a hard lock rejecting `POST /api/alarm` within 1
  hour of the next scheduled firing — enforced server-side on the ESP32
  so it holds even with no phone connected.
- **Vue app**: full UI — battery %, alarm time, locked/unlocked state,
  live status (polling `/api/status`, or a WebSocket via microdot's async
  support if polling feels laggy).
- **Bulb integration**: pick back up the LocalTuya approach above.
- **Docs**: keep this file updated as the design evolves.

## Notes / deferred decisions

- No authentication on the web UI for now — fine for a home hobby
  project; a simple shared PIN is a reasonable Phase B addition if it
  ever matters.
- WiFi credential provisioning is hardcoded for Phase A; a first-boot
  setup flow (served from the same web UI, or a temporary AP mode) is a
  reasonable Phase B improvement.
- **`microdot` only serves plain HTTP (port 80), no TLS.** Some mobile
  browsers (Android Chrome with "Always use secure connections", HSTS
  from a previous visit, etc.) silently auto-upgrade a typed address to
  `https://`, which then gets refused/reset against this server and looks
  identical to "the device isn't responding." Always type `http://` (not
  just the bare IP) explicitly when testing from a phone.
- **Gotcha found while debugging Phase A boot crashes**: MicroPython
  compiles a whole `.py` file's constants (including large string
  literals and its imports) into RAM before executing any of it —
  regardless of where in the file they appear. A large inline HTML/JS
  string plus the `microdot` import were starving `network.WLAN()` of the
  internal RAM it needs for its WiFi RX buffers, causing
  `OSError: WiFi Out of Memory` at boot. Fix: keep `main.py` minimal
  (just the WiFi connect logic) and move the web app / `microdot` import
  into a separate `webapp.py`, imported only *after* WiFi is already
  connected.
- Weak WiFi signal (RSSI below roughly -75/-80) doesn't just slow things
  down on this board — it was also correlated with an early async-server
  crash (`OSError: [Errno 5] EIO`). A consistently weak signal at the
  final mounting location would still need a real fix (move the router,
  add a mesh node/extender) before Phase B.
- **Dropped `microdot`'s `asyncio`-based server, replaced with a plain
  synchronous socket server** (see `esp32/webapp.py`). `microdot`'s
  `app.run()` reproducibly failed with `OSError: -203` from inside
  `asyncio.start_server()` on every single boot on this firmware
  (`ESP32_GENERIC-20260824-v1.29.0.bin`) — but extensive isolated testing
  via `mpremote exec` (raw socket bind/listen, bare `asyncio.start_server`
  with identical args, even microdot's exact two-call
  TypeError/fallback sequence) always succeeded standalone. Root cause
  not fully identified (suspected some difference between code
  auto-running at boot vs. invoked live over an already-open REPL
  session); rather than keep chasing it, `webapp.py` now does its own
  minimal blocking HTTP server with `socket` directly — proven reliable
  in testing. `microdot.py` is left in the repo but unused; revisit if a
  future MicroPython/firmware update seems likely to have fixed this.

## Verification (Phase A)

- REPL/serial output shows the ESP32 joining WiFi and printing its
  static IP.
- A phone browser on the same WiFi loads the Vue page from that IP and
  successfully round-trips a call to `/api/ping`.
- Switch presses are logged over serial; the buzzer produces an audible
  test tone — confirms wiring independent of the web-server path.
