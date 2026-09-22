import time
from machine import Pin, PWM
from esp32 import RMT

SWITCH_PIN = 4
BUZZER_PIN = 27

# IR blaster -- controls the IR-remote RGB bulb. Bare LED direct off the
# GPIO through a current-limiting resistor (no transistor yet): GPIO23 ->
# 100ohm resistor -> LED anode (long leg) -> LED cathode (short leg) -> GND.
# Fine for close-range bench testing; add an NPN transistor driver later if
# more range/current is needed (see PLAN.md notes on the IR bulb pivot).
IR_LED_PIN = 23

# RGB status LED -- replaces the old single-color LED. Common anode: COM ->
# 3.3V, so each color pin must be driven LOW (sinking current) to turn that
# color on -- the opposite of common cathode. See set_rgb()'s inverted duty
# mapping below.
RGB_R_PIN = 33
RGB_G_PIN = 25
RGB_B_PIN = 26
# 1000Hz used to sit squarely in the audible range -- Idle's breathing and
# Before's rainbow drive this PWM continuously, and electrical crosstalk
# from it into the buzzer's own circuit (shared power/ground rails) is the
# likely cause of the soft-beeping/static noise heard during those two
# states even though nothing in states.py ever touches the buzzer there.
# Well above human hearing range now, so even if some coupling remains
# it's no longer audible.
RGB_PWM_FREQ = 20000

# 7-LED bar graph (quantity indicator), driven off a 7-segment/bar array
# package with 8 pins (7 segments + 1 common). Common anode: COM -> 3.3V
# through one shared resistor, each segment pin LOW = on (GPIO sinks it).
BAR_COMMON_CATHODE = False
# a->22, b->21, c->19, d->18, e->5, f->17, g->16 -- see pin-map.html's
# "cleaned up" diagram: all 7 segments read top-to-bottom off the 3.3V
# header column instead of two of them (old b/c) sitting on the left side.
BAR_PINS = [22, 21, 19, 18, 5, 17, 16]

switch = Pin(SWITCH_PIN, Pin.IN, Pin.PULL_UP)
buzzer = PWM(Pin(BUZZER_PIN), freq=2000, duty_u16=0)

rgb_r = PWM(Pin(RGB_R_PIN), freq=RGB_PWM_FREQ, duty_u16=0)
rgb_g = PWM(Pin(RGB_G_PIN), freq=RGB_PWM_FREQ, duty_u16=0)
rgb_b = PWM(Pin(RGB_B_PIN), freq=RGB_PWM_FREQ, duty_u16=0)

bar_leds = [Pin(pin, Pin.OUT) for pin in BAR_PINS]

# clock_div=80 on the ESP32's 80MHz APB clock gives a 1MHz RMT tick (1 tick
# = 1us), so the NEC timing constants below (in microseconds) can be used
# directly as tick counts with no conversion. tx_carrier modulates every
# "mark" (odd-indexed, carrier_level=1) pulse at 38kHz/33% duty -- the
# carrier NEC receivers demodulate -- so write_pulses only needs to supply
# the on/off envelope timing, not the carrier itself.
_ir_rmt = RMT(0, pin=Pin(IR_LED_PIN, Pin.OUT), clock_div=80, tx_carrier=(38000, 33, 1))

# Tracks what's currently driven so set_tone/silence only touch the PWM
# registers on an actual change -- reconfiguring buzzer.freq() every tick
# even at duty=0 was producing an audible click/pop each time.
_current_freq = None
_current_duty = 0


def set_tone(freq, duty):
    global _current_freq, _current_duty
    freq = max(freq, 1)
    if freq != _current_freq:
        buzzer.freq(freq)
        _current_freq = freq
    if duty != _current_duty:
        buzzer.duty_u16(duty)
        _current_duty = duty


def silence():
    global _current_duty
    if _current_duty != 0:
        buzzer.duty_u16(0)
        _current_duty = 0


def beep(duration_ms=200, freq=2000, duty_u16=32768):
    set_tone(freq, duty_u16)
    time.sleep_ms(duration_ms)
    silence()


def tone_for_percent(pct, buzzer_cfg):
    """Map an Agro-style 0-100 value to (freq_hz, duty_u16) per the
    configured min/max range -- the curve Action uses to drive the buzzer
    from the live Agro value."""
    pct = max(0, min(100, pct))
    min_freq = buzzer_cfg["minFreqHz"]
    max_freq = buzzer_cfg["maxFreqHz"]
    min_duty = buzzer_cfg["minDutyPct"]
    max_duty = buzzer_cfg["maxDutyPct"]
    freq = int(min_freq + (max_freq - min_freq) * pct / 100)
    duty_pct = min_duty + (max_duty - min_duty) * pct / 100
    return max(freq, 1), int(duty_pct / 100 * 65535)


def sweep_buzzer(buzzer_cfg, steps=10, step_ms=150):
    for i in range(steps + 1):
        freq, duty = tone_for_percent(i * 100 / steps, buzzer_cfg)
        set_tone(freq, duty)
        time.sleep_ms(step_ms)
    silence()


def set_rgb(r_pct, g_pct, b_pct):
    """r/g/b_pct each 0-100. Common anode -> the pin sinks current to turn
    a color on, so brightness is inverted: 100% duty (pin always HIGH,
    same potential as COM) is OFF, 0% duty (pin always LOW) is fully ON."""
    rgb_r.duty_u16(65535 - int(max(0, min(100, r_pct)) / 100 * 65535))
    rgb_g.duty_u16(65535 - int(max(0, min(100, g_pct)) / 100 * 65535))
    rgb_b.duty_u16(65535 - int(max(0, min(100, b_pct)) / 100 * 65535))


def set_bar(states):
    """states: iterable of up to 7 truthy/falsy values, index 0..6."""
    on_value = 1 if BAR_COMMON_CATHODE else 0
    off_value = 0 if BAR_COMMON_CATHODE else 1
    for pin, state in zip(bar_leds, states):
        pin.value(on_value if state else off_value)


def set_bar_level(fraction):
    """Light a contiguous block of the bar starting at segment a (index 0),
    sized to fraction (0-1) of the full 7. A shrinking fraction therefore
    always empties from the g end first, and a growing one always fills
    from a first -- segments physically grow a->g on this part, so this is
    the only fill direction that reads correctly either way."""
    fraction = max(0.0, min(1.0, fraction))
    count = round(fraction * len(bar_leds))
    set_bar([i < count for i in range(len(bar_leds))])


# NEC protocol timings, in microseconds -- the near-universal format for
# cheap IR remotes (including the small "N-key" RGB LED controllers this
# bulb almost certainly ships with).
_NEC_HEADER_MARK = 9000
_NEC_HEADER_SPACE = 4500
_NEC_BIT_MARK = 560
_NEC_ONE_SPACE = 1690
_NEC_ZERO_SPACE = 560
_NEC_FINAL_MARK = 560


def _nec_pulses(address, command):
    """address/command are 8-bit. NEC sends each byte followed by its
    bitwise complement (standard error-check quirk of the protocol) --
    that's what the ^ 0xFF pairs below are."""
    pulses = [_NEC_HEADER_MARK, _NEC_HEADER_SPACE]
    for byte in (address, address ^ 0xFF, command, command ^ 0xFF):
        for bit in range(8):
            pulses.append(_NEC_BIT_MARK)
            pulses.append(_NEC_ONE_SPACE if (byte >> bit) & 1 else _NEC_ZERO_SPACE)
    pulses.append(_NEC_FINAL_MARK)
    return pulses


def ir_send_nec(address, command):
    # write_pulses is a native/positional-only call on this firmware --
    # write_pulses(pulses, start=True) raises "doesn't take keyword
    # arguments", so `start` must be passed positionally.
    _ir_rmt.write_pulses(_nec_pulses(address, command), True)


# on (0x07), off (0x06), and fade (0x1B) were all captured directly off
# the bulb's real remote via the VS1838B and cross-checked against their
# complement bytes (~0x07==0xF8, ~0x06==0xF9, ~0x1B==0xE4) -- all three
# confirmed correct. The originally-reported table's on/off (0x82/0x02)
# turned out wrong (valid NEC, just not this bulb's codes, likely a
# different remote model sharing the same 0x00 address); fade (0xC8) was
# from that same bad source and is presumed wrong too, though unverified.
IR_CODES = {
    "on": (0x00, 0x07),
    "off": (0x00, 0x06),
    "fade": (0x00, 0x1B),
    # The originally-reported (wrong, or presumed wrong) codes -- kept
    # only for reference/comparison, not used anywhere.
    "onOld": (0x00, 0x82),
    "offOld": (0x00, 0x02),
    "fadeOld": (0x00, 0xC8),
}


def ir_command(name):
    code = IR_CODES.get(name)
    if code is None:
        print("unknown IR command:", name)
        return
    ir_send_nec(*code)


# Repeat-until state for the "hold a button down" test button -- resends a
# code every _IR_REPEAT_INTERVAL_MS for a fixed duration. Driven from
# ble_service's existing tick loop (see ir_repeat_tick) rather than
# blocking in place, since starting it happens inside a BLE IRQ callback
# and a real sleep loop there would stall BLE for the whole duration.
_IR_REPEAT_INTERVAL_MS = 100
_ir_repeat_code = None
_ir_repeat_until_ms = None
_ir_repeat_last_ms = None


def ir_repeat_start(name, duration_ms=30000):
    global _ir_repeat_code, _ir_repeat_until_ms, _ir_repeat_last_ms
    code = IR_CODES.get(name)
    if code is None:
        print("unknown IR command:", name)
        return
    _ir_repeat_code = code
    _ir_repeat_until_ms = time.ticks_add(time.ticks_ms(), duration_ms)
    _ir_repeat_last_ms = None  # forces an immediate first send below


def ir_repeat_tick():
    """Call this often (every tick_forever iteration) -- sends one repeat
    frame every _IR_REPEAT_INTERVAL_MS while a repeat window is active, a
    no-op otherwise."""
    global _ir_repeat_code, _ir_repeat_last_ms
    if _ir_repeat_code is None:
        return
    now = time.ticks_ms()
    if time.ticks_diff(now, _ir_repeat_until_ms) >= 0:
        _ir_repeat_code = None
        return
    if _ir_repeat_last_ms is not None and time.ticks_diff(now, _ir_repeat_last_ms) < _IR_REPEAT_INTERVAL_MS:
        return
    ir_send_nec(*_ir_repeat_code)
    _ir_repeat_last_ms = now


# Fire-and-forget queue of IR commands sent one at a time with a gap
# between each -- e.g. Before's wake-up trigger (On, On, Fade, Fade).
# Same non-blocking reasoning as the repeat mechanism above: this gets
# started from inside states.py's tick handling, which must never block,
# so the actual sends are spread out via ir_sequence_tick() instead.
_IR_SEQUENCE_GAP_MS = 300  # give the bulb's receiver a beat between presses
_ir_sequence_queue = []
_ir_sequence_next_ms = None


def ir_sequence_start(names):
    global _ir_sequence_queue, _ir_sequence_next_ms
    _ir_sequence_queue = list(names)
    _ir_sequence_next_ms = time.ticks_ms()  # send the first one on the next tick


def ir_sequence_tick():
    """Call every tick -- sends the next queued command once
    _IR_SEQUENCE_GAP_MS has passed since the last one, a no-op if the
    queue is empty."""
    global _ir_sequence_next_ms
    if not _ir_sequence_queue:
        return
    if time.ticks_diff(time.ticks_ms(), _ir_sequence_next_ms) < 0:
        return
    ir_command(_ir_sequence_queue.pop(0))
    _ir_sequence_next_ms = time.ticks_add(time.ticks_ms(), _IR_SEQUENCE_GAP_MS)
