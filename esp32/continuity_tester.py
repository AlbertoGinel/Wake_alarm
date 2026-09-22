"""Bench tool, not part of the alarm firmware. Run standalone on a SPARE
ESP32 with `mpremote run continuity_tester.py` (streams and runs live,
doesn't touch the spare board's filesystem). Automatically scans every
pair of the wired-up DUT (device under test = the alarm board) points for
a short, with no manual probing -- wire it all up once, then just watch
the console.

How it works: each DUT point is connected by one jumper wire to one of
this spare board's GPIOs (table below). The script cycles through driving
each of those GPIOs LOW in turn (all the others sit on their internal
pull-up); if a DUT point is actually shorted to the one being driven, its
paired GPIO gets pulled low too, and that's reported. Runs forever, so you
can watch results update live while re-touching a joint with an iron.

SAFETY: the alarm board (DUT) must be completely unpowered -- no USB, no
battery -- for the whole time you're wired up. We're only reading passive
copper/solder here; a live chip on the other end would produce garbage
(or damage something).

Default point list below covers the header run around the new bar-array
soldering (see pin-map.html's "cleaned up" diagram) plus GND/3V3, since a
short to either rail is just as likely as segment-to-segment. Trim the
list if you don't have that many jumper wires spare -- the highest-
suspicion cluster is the first four rows (GPIO22/1/3/21), since those are
the ones physically sandwiching the USB-serial TX/RX pins.
"""
import time
from machine import Pin

# (this spare board's scanner GPIO, label of the alarm-board point it's wired to)
TEST_POINTS = [
    (5,  "DUT GPIO22 (Bar a)"),
    (13, "DUT GPIO1 (serial TX)"),
    (14, "DUT GPIO3 (serial RX)"),
    (16, "DUT GPIO21 (Bar b)"),
    (4,  "DUT GPIO23 (IR TX)"),
    (17, "DUT GPIO19 (Bar c)"),
    (18, "DUT GPIO18 (Bar d)"),
    (19, "DUT GPIO5 (Bar e)"),
    (21, "DUT GPIO17 (Bar f)"),
    (22, "DUT GPIO16 (Bar g)"),
    (23, "DUT GPIO4 (Switch)"),
    (25, "DUT GPIO2 (strap/free)"),
    (26, "DUT GPIO15 (strap/free)"),
    (27, "DUT GND"),
    (32, "DUT 3V3 (bar COM)"),
]

SCAN_INTERVAL_S = 2


def _scan():
    pins = {gpio: Pin(gpio, Pin.IN, Pin.PULL_UP) for gpio, _ in TEST_POINTS}
    shorts = []
    for out_gpio, out_label in TEST_POINTS:
        driver = Pin(out_gpio, Pin.OUT)
        driver.value(0)
        time.sleep_ms(5)
        for in_gpio, in_label in TEST_POINTS:
            if in_gpio == out_gpio:
                continue
            if pins[in_gpio].value() == 0:
                shorts.append(tuple(sorted((out_label, in_label))))
        pins[out_gpio] = Pin(out_gpio, Pin.IN, Pin.PULL_UP)  # release before the next driven pin
    return set(shorts)


print("Automatic short scanner -- wire each spare-board GPIO to its DUT point:")
for gpio, label in TEST_POINTS:
    print("  GPIO{:<3} -> {}".format(gpio, label))
print()
print("DUT (alarm board) must be fully unpowered. Scanning every {}s, Ctrl-C to stop.".format(SCAN_INTERVAL_S))
print()

_seen = set()
while True:
    current = _scan()
    for pair in current - _seen:
        print("SHORT DETECTED: {} <-> {}".format(*pair))
    for pair in _seen - current:
        print("cleared: {} <-> {}".format(*pair))
    if not current and not _seen:
        print(".", end="")
    _seen = current
    time.sleep(SCAN_INTERVAL_S)
