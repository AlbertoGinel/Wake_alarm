# Alarm Project

> **Status: superseded.** This document described the original plan (a
> Fairphone 2 pinned as a single-app proximity clock). That plan is no
> longer active — the FP2's USB data port turned out to be faulty, and a
> Samsung GT-S7390 stand-in was ruled out entirely (too old for Flutter
> or Screen Pinning; see the Hardware Diagnostics section below). The
> project has pivoted to dedicated **ESP32 hardware** as the actual alarm
> device, with the phone reduced to a normal remote-control app — no
> kiosk/App Pinning needed. See `PLAN.md` (or the current plan file) for
> the active design. The sections below are kept for historical context.

## Fairphone 2 — Single-App Proximity Clock (superseded)

## Goal

Turn a Fairphone 2 into a dedicated single-app device, and a learning
project for Flutter: a clock that stays blank/idle until the phone is
brought close to something (the same **proximity sensor** used during
calls to detect the phone against your head/ear), then shows the time.
The device must remain **fully reversible** — no root, no custom ROM, no
bootloader unlock. At any point the phone should be able to go back to
being a normal Fairphone OS device.

## Constraints

- No root, no unlocked bootloader, no custom recovery.
- No camera-based detection — the earpiece **proximity sensor**
  (`TYPE_PROXIMITY`), not the accelerometer.
- Reversible: unpinning/uninstalling restores normal phone behavior
  immediately.
- Runs on 2015-era hardware (Snapdragon 801, 2GB RAM) — keep the runtime
  footprint small.
- Built in **Flutter**, chosen deliberately for learning purposes over
  native Android.

## Hardware Diagnostics & Decisions Log

**FP2 USB data port — broken, needs fixing (not a cable/laptop issue).**
- The FP2 charges fine over USB but never enumerates as a data device:
  `adb devices` returns empty, and `lsusb`/`dmesg` show the port
  repeatedly failing to enumerate (`Device not responding to setup
  address`, `error -71`, `unable to enumerate USB device`).
- Ruled out: tried 2 different cables and 2 different laptop USB ports,
  same failure every time. Other USB peripherals on the same laptop
  (mouse, webcam, SD card reader) enumerate fine on the same ports, so
  the laptop's USB controller/ports are not the problem.
- Conclusion: the fault is on the **phone's connector** — dirt/lint or a
  worn micro-USB port (a known FP2 wear point). Next steps: clean the
  port and try a wiggle-test for a marginal connection; if that doesn't
  produce a solid connection, replace the FP2's user-serviceable
  **Bottom Module** (contains the USB port, sold as a spare part by
  Fairphone/iFixit — no soldering required).
- **This matters beyond dev convenience**: the planned microcontroller
  link (see below) will also run through this same physical port in USB
  host/OTG mode. A "good enough for adb" fix isn't the bar — it needs to
  be a solid connection, since the finished device's control link
  depends on the same connector.

**Samsung GT-S7390 (Galaxy Trend) — evaluated as a stand-in, rejected.**
Its USB *data* connection to a PC works reliably (unlike the FP2's right
now), which made it tempting as a temporary substitute. But it's not
usable for this project on any axis:
- Runs Android 4.1.2 (API 16). Flutter's minimum supported Android
  version is API 21 (Lollipop) — Flutter cannot run on it at all.
- Screen Pinning / App Pinning was introduced in Android 5.0 (API 21) —
  it doesn't exist on this device's OS, so there's no built-in kiosk
  mechanism to use either.
- Its USB OTG (host mode) support — which the microcontroller link would
  need — is reportedly unreliable/hacky on this exact model (community
  reports of needing extra tools to fully enable it), on top of the two
  blockers above.
- **Decision: shelved.** Not worth building anything on, even
  temporarily. The FP2's USB port is the thing to fix, not a device
  swap.

## Future Direction — Microcontroller Link (likely ESP32)

The project is expanding beyond just the clock: physical **buttons and a
buzzer** will be driven by a microcontroller, connected to the phone over
USB. **ESP32** is the current front-runner for that microcontroller.
This isn't fully designed yet — flagging the shape of it here since it
affects the hardware/toolchain decisions above:

- The phone needs to act as a **USB host** (OTG) to talk to the
  microcontroller, which is a different mode from the USB *device* role
  the phone uses for adb/PC connections. This is the reason the FP2's
  port needs a solid fix, not just an adb-capable one (see above).
- On the Flutter side, this will need a USB-serial plugin (e.g. something
  like `usb_serial` on pub.dev, which wraps Android's USB host APIs for
  CDC/serial devices) — same caveat as the proximity-sensor plugin below:
  **verify it's maintained and matches the current Flutter/Android
  versions before committing to it.**
- ESP32 also has built-in WiFi/BLE, so a wireless link is technically an
  option instead of wired USB — noted here as a fallback, but wired USB
  is the stated preference, so that's the default plan.
- Flashing the ESP32's own firmware happens from a PC over USB
  separately — that's independent of the phone↔ESP32 runtime link and
  isn't affected by any of the above.

## Step 1 — App Pinning (Screen Pinning)

This is the built-in Android mechanism for exactly this use case, already
present in stock Fairphone OS since it ships stock-ish Android (Lollipop+).
It requires no root and is trivially reversible.

**Enable it:**
1. Settings → Security → Screen Pinning → On (optionally enable "Ask for
   PIN before unpinning" for kiosk-style security).

**Pin the clock app:**
1. Open the app.
2. Open Recents (Overview) — square/multitask button.
3. Tap the app icon at the top of its card → "Pin this app".

**Unpin (return to normal phone):**
- Hold Back + Overview simultaneously (or swipe up and hold, depending on
  nav mode), enter PIN if one was set. This drops straight back to the
  normal Fairphone launcher — no reinstall or reset needed.

This alone satisfies "single app device" and "must go back to Fairphone
OS" — it's a settings toggle, not a system modification.

## Step 2 — Framework Choice

**Chosen: Flutter (Dart), for learning purposes.**

This trades away some of the raw efficiency a native Kotlin app would give
you on 2015-era hardware, but that's an explicit, reasonable choice here
since the point of the project is to learn Flutter. Practical
implications to keep in mind:

- Android's proximity sensor isn't exposed by the mainstream
  `sensors_plus` package (that one covers accelerometer/gyroscope/
  magnetometer/barometer). You'll need a dedicated proximity plugin —
  e.g. a package like `proximity_sensor` on pub.dev, which wraps
  `TYPE_PROXIMITY` and exposes a near/far event stream. **Check pub.dev
  directly before committing to one** — plugin maintenance status and
  APIs drift, and you'll want to confirm it still supports current
  Flutter/Android versions and is maintained.
- If no proximity plugin is in good shape when you get there, the
  fallback is a small **platform channel**: a few lines of Kotlin using
  `SensorManager`/`TYPE_PROXIMITY` on the Android side, exposed to Dart
  via `MethodChannel`/`EventChannel`. This is also a good learning
  exercise in its own right (Flutter ↔ native interop).

## Step 3 — App Design

**Core loop:**
- Subscribe to the proximity sensor's near/far event stream (via plugin
  or platform channel).
- On a "near" event (phone brought close to head/hand/surface): fade in
  / wake the display and show the current time (a `Text` widget bound to
  a ticking `Stream<DateTime>` or `Timer.periodic`).
- On "far" (or after N seconds with no "near" event): fade the clock back
  out to blank.
- Keep the screen awake with a wakelock package (e.g. `wakelock_plus`),
  since Flutter has no direct equivalent of `FLAG_KEEP_SCREEN_ON` — this
  needs a plugin.

**Sensor choice detail:**
- `TYPE_PROXIMITY` is the same low-power IR sensor used to blank the
  screen during phone calls — binary near/far, not a distance value, and
  very cheap to sample continuously.
- No polling loop needed on your end: Android delivers events on state
  change, so the sensor itself is efficient even without extra
  battery-saving logic in the app.

**Key Flutter pieces:**
- `main()` → single `StatefulWidget` screen, no navigation needed
  (single-app device).
- Proximity stream subscribed in `initState`, cancelled in `dispose`.
- `wakelock_plus` (or similar) enabled while the app is foregrounded.
- No background service needed — screen pinning keeps this single
  Activity/Flutter engine in the foreground the whole time.

## Step 4 — Build & Deploy

1. `flutter create` a new project; set `minSdkVersion` in
   `android/app/build.gradle` to match FP2's Android version (check
   Settings → About Phone; FP2 shipped Android 5.1, upgradable to
   6.0/7.1.1/9 depending on ROM — check current OS version first).
2. `flutter pub add` the proximity and wakelock packages chosen in Step 2.
3. Enable Developer Options → USB debugging on the phone.
4. `flutter run` (debug, with the phone connected via USB) or
   `flutter build apk` + `adb install build/app/outputs/.../app-release.apk`
   for a standalone install — no Play Store, no signing requirements
   beyond a debug/local key for personal use.
5. No root, no unlocked bootloader required at any step.

## Step 5 — Exit / Revert Path

- Unpin via Back+Overview (see Step 1) → back to normal launcher instantly.
- Uninstall the app via Settings → Apps if no longer needed.
- Disable Screen Pinning in Settings → Security if desired.
- None of the above touches system partitions — the phone is a stock
  Fairphone OS device at every point, just with a pinned app on top.

## Open Questions

- Confirm current Fairphone OS / Android version on the device (affects
  minSdk and available sensor APIs).
- Confirm a maintained Flutter proximity-sensor plugin exists for the
  target Flutter/Android version, or plan for the platform-channel
  fallback from the start.
- Decide idle-timeout duration after a "far" event — will need
  on-device tuning.
- Decide whether PIN-protected unpinning is wanted (kiosk security) or
  plain screen pinning (easy exit) is fine for this use case.
- Confirm the FP2's USB port is solidly fixed (cleaning or Bottom Module
  replacement) before relying on it for the microcontroller link.
- Decide on the ESP32 (or other microcontroller) and the serial protocol
  between it and the phone once the port issue is resolved.
