import math
import time
import urandom

import hardware
import history
import linkstatus
import storage

IDLE = "idle"
LOADED = "loaded"
BEFORE = "before"
ACTION = "action"
BUTTON = "button"
WIN = "win"

_UNLOCKED_STATES = (IDLE, LOADED)

# How much of a *test* load's total window to spend in Loaded before
# moving into Before -- kept short and fixed so even a short test window
# still visibly exercises both states, rather than using the real (often
# hour-long) config["timeBeforeSec"] and skipping straight past Loaded
# into Before. A real arm() always uses the real config value instead.
TEST_LOADED_SEC = 3


def test_before_sec(in_seconds):
    return max(1, in_seconds - TEST_LOADED_SEC)

# Debounce: only trust the switch reading once this many consecutive
# ticks (roughly one every 100ms, see webapp.run's tick loop) agree.
_DEBOUNCE_SAMPLES = 3
_debounce_history = []

# Monotonic (not wall-clock) elapsed time since the previous tick, so a
# clock jump from an NTP resync can't corrupt a single tick's pacing math.
_last_tick_ms = None

# All of the below are ephemeral light/sound phase accumulators -- display
# effects only, never persisted, reset on the relevant state entry so they
# always restart cleanly (see the _enter_* functions).
_beep_phase = 0.0          # position within the current buzzer beep cycle
_blink_phase = 0.0         # position within the current RGB blink cycle
_idle_phase = 0.0          # position within the idle breathing cycle
_before_hue = 0.0          # current hue (0-360) of Before's rainbow cycle
_loaded_flash_elapsed = 0.0  # time since Loaded's one-time confirm flash began
_win_elapsed = 0.0         # time since Win started (drives the fanfare)
_win_flicker_elapsed = 0.0  # time since the bar's last chaotic flicker frame

# Idle/Before push a new RGB color on literally every tick (50/sec) to
# animate smoothly, but each duty-register write is a small step change
# with broadband electrical content -- audible as a regular click/hiss
# coupling into the buzzer circuit even now that the PWM carrier itself
# (see hardware.RGB_PWM_FREQ) is well above hearing range. Throttling the
# actual hardware writes to a slower rate cuts that click rate down while
# the animation's own timing (idle_phase/before_hue) keeps accumulating
# every tick regardless, so it still looks smooth.
_RGB_PUSH_INTERVAL_SEC = 0.1
_rgb_push_elapsed = 0.0

# Bench-test RGB override: (r, g, b, expires_at_ms) or None. Wins over
# whatever the current state's own tick handler sets, for a fixed window --
# lets the webapp's RGB test picker actually hold a color instead of being
# stomped every 20ms by e.g. Idle's breathing status light.
RGB_TEST_OVERRIDE_MS = 10000
_rgb_test_override = None

# Same idea, for the bar -- Idle's own tick handler redrives the bar every
# tick too (e.g. hardware.set_bar([_button_pressed()] * 7)), which would
# otherwise stomp a test pattern within one 20ms tick, not even long
# enough to see it.
BAR_TEST_OVERRIDE_MS = 2000
_bar_test_override = None


def is_locked(memory):
    return memory["session"]["state"] not in _UNLOCKED_STATES


def load_alarm(memory, wake_up_epoch, before_sec=None):
    """Idle -> Loaded. Only valid while unlocked (Idle or Loaded itself,
    to allow re-arming with a new time before Before starts).

    before_sec overrides config["timeBeforeSec"] for this armed instance
    only -- used by the test-load path (see test_before_sec) so a short
    test window still passes through Before instead of jumping straight
    from Loaded to Action. A real arm leaves this None and gets the
    configured production value."""
    global _loaded_flash_elapsed
    if is_locked(memory):
        raise ValueError("cannot load while locked")
    config = memory["config"]
    session = memory["session"]
    session["state"] = LOADED
    session["wakeUpTimeEpoch"] = wake_up_epoch
    session["beforeSec"] = before_sec if before_sec is not None else config["timeBeforeSec"]
    session["buffer"] = 100
    session["agro"] = 0
    session["hasPressedOnce"] = False
    # Grown above config.tempIntervalSec if recent days were rated "bad" --
    # see history.calculated_hold_sec. Computed once here, not re-derived
    # every tick, so a rating change mid-countdown can't retroactively
    # shift the goalposts on an already-running hold.
    hold_total = history.calculated_hold_sec(memory, time.time())
    session["holdRemainingSec"] = hold_total
    session["holdTotalSec"] = hold_total
    storage.save(memory)
    _loaded_flash_elapsed = 0.0  # (re)start the confirm flash
    hardware.set_rgb(0, 0, 0)


def cancel_to_idle(memory):
    """Loaded -> Idle. The one manual way back -- not available once
    Before has started (is_locked() gates this from the HTTP layer too,
    but this is the state check that actually matters)."""
    if memory["session"]["state"] != LOADED:
        raise ValueError("can only cancel from loaded")
    _enter_idle(memory)


def force_idle(memory):
    """Dev-only escape hatch: jump to Idle from ANY state, bypassing the
    lock entirely. Not a real transition in the diagram -- the finished
    product has no way back once Before starts, on purpose. This exists
    only because session.state deliberately survives a reboot (so a real
    crash mid-alarm doesn't lose the schedule), which means a physical
    reset can't be used to get unstuck during testing either."""
    _enter_idle(memory)


def set_rgb_test_override(r, g, b, duration_ms=RGB_TEST_OVERRIDE_MS):
    """Bench-test only: hold an RGB color for duration_ms regardless of
    what the current alarm state would otherwise drive it to. Applied
    immediately and then re-asserted every tick until it expires (see
    _apply_rgb_test_override), so it survives Idle's breathing cycle,
    Before's rainbow, etc."""
    global _rgb_test_override
    _rgb_test_override = (r, g, b, time.ticks_add(time.ticks_ms(), duration_ms))
    hardware.set_rgb(r, g, b)


def _apply_rgb_test_override():
    global _rgb_test_override
    if _rgb_test_override is None:
        return
    r, g, b, expires_ms = _rgb_test_override
    if time.ticks_diff(time.ticks_ms(), expires_ms) >= 0:
        _rgb_test_override = None
        return
    hardware.set_rgb(r, g, b)


def set_bar_test_override(bar_states, duration_ms=BAR_TEST_OVERRIDE_MS):
    """Bench-test only: hold a bar pattern for duration_ms regardless of
    what the current alarm state would otherwise drive it to. Same
    reapply-every-tick pattern as set_rgb_test_override above."""
    global _bar_test_override
    _bar_test_override = (list(bar_states), time.ticks_add(time.ticks_ms(), duration_ms))
    hardware.set_bar(bar_states)


def _apply_bar_test_override():
    global _bar_test_override
    if _bar_test_override is None:
        return
    bar_states, expires_ms = _bar_test_override
    if time.ticks_diff(time.ticks_ms(), expires_ms) >= 0:
        _bar_test_override = None
        return
    hardware.set_bar(bar_states)


def tick(memory, now=None):
    now = now if now is not None else time.time()
    dt = _dt()
    _sample_button()
    session = memory["session"]
    config = memory["config"]
    handler = _HANDLERS.get(session["state"])
    if handler:
        handler(memory, config, session, now, dt)
    _apply_rgb_test_override()
    _apply_bar_test_override()


def _dt():
    global _last_tick_ms
    now_ms = time.ticks_ms()
    if _last_tick_ms is None:
        _last_tick_ms = now_ms
        return 0.0
    elapsed = time.ticks_diff(now_ms, _last_tick_ms) / 1000
    _last_tick_ms = now_ms
    return elapsed


def _sample_button():
    _debounce_history.append(hardware.switch.value() == 0)
    if len(_debounce_history) > _DEBOUNCE_SAMPLES:
        del _debounce_history[0]


def _button_pressed():
    return (len(_debounce_history) == _DEBOUNCE_SAMPLES
            and all(_debounce_history))


def _button_released():
    return (len(_debounce_history) == _DEBOUNCE_SAMPLES
            and not any(_debounce_history))


def _network_ok():
    """BLE's own connection state IS the ground truth here, updated
    instantly by ble_service's IRQ handler -- no heartbeat/staleness-window
    guessing needed the way the old WiFi/HTTP transport (netstatus.py,
    kept but unused) required."""
    return linkstatus.connected


def _blink(period, dt):
    """Advance the shared blink phase and return whether it's in its 'on'
    half this tick. period is read fresh every call so a caller can vary
    it live (Action speeds this up as intensity rises)."""
    global _blink_phase
    period = max(period, 0.01)
    _blink_phase = (_blink_phase + dt) % (period * 2)
    return _blink_phase < period


def _push_rgb_throttled(r, g, b, dt):
    """For Idle/Before's continuous smooth animations only -- see
    _RGB_PUSH_INTERVAL_SEC above for why this exists. Action/Win call
    hardware.set_rgb() directly since their blink transitions need to
    land immediately, not on the next throttled window."""
    global _rgb_push_elapsed
    _rgb_push_elapsed += dt
    if _rgb_push_elapsed >= _RGB_PUSH_INTERVAL_SEC:
        _rgb_push_elapsed = 0.0
        hardware.set_rgb(r, g, b)


def _hue_to_rgb_pct(hue_deg):
    """Full-saturation, full-brightness HSV->RGB at the given hue (0-360),
    returned as 0-100 percentages -- hardware.set_rgb's native scale."""
    hue_deg = hue_deg % 360
    h = hue_deg / 60
    x = 100 * (1 - abs(h % 2 - 1))
    if h < 1:
        return 100, x, 0
    if h < 2:
        return x, 100, 0
    if h < 3:
        return 0, 100, x
    if h < 4:
        return 0, x, 100
    if h < 5:
        return x, 0, 100
    return 100, 0, x


def _play_melody(elapsed_sec, melody, duty_pct):
    elapsed_ms = elapsed_sec * 1000
    t = 0
    for freq, dur_ms in melody:
        if elapsed_ms < t + dur_ms:
            hardware.set_tone(freq, int(duty_pct / 100 * 65535))
            return
        t += dur_ms
    hardware.silence()


def _enter_idle(memory):
    global _idle_phase
    storage.reset_session(memory)
    _idle_phase = 0.0
    hardware.set_rgb(0, 0, 0)
    hardware.set_bar([False] * 7)
    hardware.silence()


def _enter_before(memory):
    global _before_hue
    _before_hue = 0.0
    memory["session"]["state"] = BEFORE
    storage.save(memory)
    # Wake the actual bulb alongside the onboard LED's rainbow: On twice
    # (in case the first press is missed) then Fade twice to kick off its
    # own color-cycling effect. Fire-and-forget, spread out over time by
    # hardware.ir_sequence_tick() so this doesn't block the tick loop.
    hardware.ir_sequence_start(["on", "on", "fade", "fade"])


def _enter_action(memory):
    global _beep_phase, _blink_phase
    _beep_phase = 0.0
    _blink_phase = 0.0
    memory["session"]["state"] = ACTION
    storage.save(memory)


def _enter_button_state(memory):
    memory["session"]["state"] = BUTTON
    memory["session"]["hasPressedOnce"] = True
    storage.save(memory)
    hardware.silence()


def _enter_win(memory):
    global _win_elapsed, _win_flicker_elapsed, _blink_phase
    session = memory["session"]
    session["state"] = WIN
    session["winStartedAt"] = time.time()
    storage.save(memory)
    _win_elapsed = 0.0
    _win_flicker_elapsed = 0.0
    _blink_phase = 0.0


def _tick_idle(memory, config, session, now, dt):
    global _idle_phase
    period = config["lights"]["idleFadePeriodSec"]
    _idle_phase = (_idle_phase + dt) % period
    brightness = (1 - math.cos(2 * math.pi * _idle_phase / period)) / 2 * 100

    # Priority order: not knowing the time is the critical failure (no RTC
    # battery -- time.time() is meaningless until the phone has pushed its
    # clock over BLE at least once), so it outranks not being paired.
    if not linkstatus.time_known:
        color = (100, 0, 0)    # red -- can't safely run yet, waiting on the phone's clock
    elif not _network_ok():
        color = (100, 100, 0)  # yellow -- no phone currently paired over BLE
    else:
        color = (0, 0, 100)    # blue -- all good
    r, g, b = color
    _push_rgb_throttled(r * brightness / 100, g * brightness / 100, b * brightness / 100, dt)

    # Bench-test indicator: light the whole bar solid while the switch is
    # held, so a wiring/soldering check doesn't need the webapp open at
    # all. Idle-only on purpose -- Action/Button already drive the bar
    # with real buffer/agro feedback, and holding the button there means
    # something else entirely.
    hardware.set_bar([_button_pressed()] * 7)


def _tick_loaded(memory, config, session, now, dt):
    global _loaded_flash_elapsed
    lights_cfg = config["lights"]
    on_period = lights_cfg["loadedBlinkPeriodSec"]
    flash_total = on_period * 2 * lights_cfg["loadedBlinkCount"]
    if _loaded_flash_elapsed < flash_total:
        _loaded_flash_elapsed += dt
        if _loaded_flash_elapsed >= flash_total:
            hardware.set_bar([False] * 7)
        else:
            on = (_loaded_flash_elapsed % (on_period * 2)) < on_period
            hardware.set_bar([on] * 7)

    if now >= session["wakeUpTimeEpoch"] - session["beforeSec"]:
        _enter_before(memory)


def _tick_before(memory, config, session, now, dt):
    global _before_hue
    wake = session["wakeUpTimeEpoch"]

    # Solid color the whole state, slowly cycling through the rainbow --
    # no blinking, and no connection check: connectivity only matters in
    # Idle (that's when you'd configure something), and Before can't be
    # unarmed anyway, so there's nothing a disconnect warning here would
    # let you act on.
    period = config["lights"]["beforeRainbowPeriodSec"]
    _before_hue = (_before_hue + 360 * dt / period) % 360
    r, g, b = _hue_to_rgb_pct(_before_hue)
    _push_rgb_throttled(r, g, b, dt)

    if now >= wake:
        _enter_action(memory)


def _tick_action(memory, config, session, now, dt):
    global _beep_phase

    session["buffer"] = max(0, session["buffer"] - config["bufferDownPace"] * dt)
    if session["buffer"] <= 0:
        session["agro"] = min(100, session["agro"] + config["agroUpPace"] * dt)

    buzzer_cfg = config["buzzer"]
    if session["buffer"] > 0 and session["hasPressedOnce"]:
        # Real silence, not just the gentle end of the curve: once you've
        # pressed the button at least once, a buffer-recovery window (e.g.
        # a bathroom trip) is a genuine grace period with no nagging. If
        # you don't make it back before buffer drains again, agro -- which
        # never resets -- picks back up from wherever it left off and
        # WILL beep; that's the actual "don't go back to bed" enforcement,
        # not a continuous buffer-phase floor.
        hardware.silence()
    else:
        # buffer > 0 (and never pressed yet) always wins and forces the
        # gentlest end of the curve for the BEEP -- but agro itself is
        # never reset, so if a prior Button hold let buffer recover before
        # agro fully decayed, agro picks back up from that same leftover
        # value once buffer next reaches 0.
        beep_intensity = session["agro"] if session["buffer"] <= 0 else 0
        beep_dur = buzzer_cfg["beepDurationMs"] / 1000
        # period shrinks from beepPeriodSec (intensity=0) down to beep_dur
        # itself (intensity=100, i.e. back-to-back beeps = a continuous tone)
        period = beep_dur + (buzzer_cfg["beepPeriodSec"] - beep_dur) * (1 - beep_intensity / 100)

        _beep_phase = (_beep_phase + dt) % period
        if _beep_phase < beep_dur:
            freq, duty = hardware.tone_for_percent(beep_intensity, buzzer_cfg)
            hardware.set_tone(freq, duty)
        else:
            hardware.silence()

    # Orange while draining the buffer, red once agro takes over -- same
    # buffer-wins split as the beep above, just visually. Blink speeds up
    # as intensity (0-100 within whichever phase is active) climbs.
    lights_cfg = config["lights"]
    if session["buffer"] > 0:
        intensity = 100 - session["buffer"]
        color = (100, 50, 0)
        bar_fraction = session["buffer"] / 100
    else:
        intensity = session["agro"]
        color = (100, 0, 0)
        bar_fraction = session["agro"] / 100
    blink_period = (lights_cfg["actionBlinkMaxPeriodSec"]
                    + (lights_cfg["actionBlinkMinPeriodSec"] - lights_cfg["actionBlinkMaxPeriodSec"])
                    * (intensity / 100))
    on = _blink(blink_period, dt)
    r, g, b = color
    hardware.set_rgb(r if on else 0, g if on else 0, b if on else 0)
    hardware.set_bar_level(bar_fraction)

    if _button_pressed():
        _enter_button_state(memory)


def _tick_button(memory, config, session, now, dt):
    session["agro"] = max(0, session["agro"] - config["agroDownPace"] * dt)
    session["buffer"] = min(100, session["buffer"] + config["bufferUpPace"] * dt)
    session["holdRemainingSec"] = max(0, session["holdRemainingSec"] - dt)

    hardware.set_rgb(0, 100, 0)
    hardware.set_bar_level(session["holdRemainingSec"] / session["holdTotalSec"])

    if session["holdRemainingSec"] <= 0:
        _enter_win(memory)
    elif _button_released():
        _enter_action(memory)


def _tick_win(memory, config, session, now, dt):
    global _win_elapsed, _win_flicker_elapsed
    lights_cfg = config["lights"]

    _win_elapsed += dt
    _play_melody(_win_elapsed, lights_cfg["winMelody"], lights_cfg["winMelodyDutyPct"])

    on = _blink(lights_cfg["winBlinkPeriodSec"], dt)
    hardware.set_rgb(0, 100 if on else 0, 0)

    _win_flicker_elapsed += dt
    if _win_flicker_elapsed >= lights_cfg["winFlickerPeriodSec"]:
        _win_flicker_elapsed = 0.0
        bits = urandom.getrandbits(7)
        hardware.set_bar([(bits >> i) & 1 for i in range(7)])

    if now - session["winStartedAt"] >= config["winDurationSec"]:
        hardware.silence()
        _enter_idle(memory)


_HANDLERS = {
    IDLE: _tick_idle,
    LOADED: _tick_loaded,
    BEFORE: _tick_before,
    ACTION: _tick_action,
    BUTTON: _tick_button,
    WIN: _tick_win,
}
