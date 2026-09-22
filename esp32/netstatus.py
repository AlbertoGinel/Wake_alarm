import time
import machine
import network
import ntptime
import ubinascii
import _thread

WIFI_SSID = "NOKIA-EC61"
WIFI_PASSWORD = "JC6bArchrf"

# Minimum time between connect() calls -- re-issuing connect() while one is
# already mid-handshake can abort it and leave the radio looping forever
# without ever finishing association ("wifi:sta is connecting, cannot set
# config" in the ESP-IDF log is exactly that happening). Association can
# legitimately take a few seconds, especially on a weak signal, so this is
# deliberately patient rather than clever about detecting "still connecting".
_RETRY_INTERVAL_MS = 8000

# How many consecutive connect() failures to tolerate before forcing a full
# WLAN interface teardown/reinit (active(False) then active(True) again)
# instead of just calling connect() again. Plain retries never recover from
# "Wifi Internal State Error" -- that's the ESP-IDF WiFi task itself stuck,
# seen so far only on a true cold power-on (not a warm EN-button reset,
# which starts the radio init fresh anyway). A full interface reset is the
# software equivalent of what EN was doing to unstick it.
_RADIO_RESET_AFTER_FAILURES = 3

# True once WLAN reports connected right now. False the instant it drops --
# no debounce, since Idle's light is meant to reflect this live.
connected = False

# Signal strength (dBm) while connected, None otherwise. Being associated
# doesn't mean requests reliably get through -- a weak signal causes
# exactly the "first request took forever, the rest got no answer" pattern
# (packets dropped and retried at the TCP layer) without ever fully
# disassociating, so connected alone can't catch it.
rssi = None
_WEAK_RSSI_DBM = -75


def signal_ok():
    return rssi is not None and rssi > _WEAK_RSSI_DBM

# True once ntptime.settime() has succeeded at least once since boot. Never
# reset back to False afterwards: there's no RTC battery, so time.time() is
# meaningless before the first sync, but once seeded it keeps ticking
# correctly on its own even through later WiFi drops.
time_known = False

_wlan = None

# Two DIFFERENT heartbeats from the HTTP server loop (webapp.run()) --
# deliberately not the same signal:
#
# - server_alive: only marked when a real client actually got accepted at
#   the TCP level. This is what the light/webpage panel show -- "blue"
#   means someone has genuinely reached the board recently, not just that
#   the loop happened to wake up on its own 10s accept() timeout with
#   nobody there. Correctly goes stale (yellow) if nobody's visited in a
#   while, which is honest, not a false alarm: it means "unproven", not
#   "broken".
#
# - loop_alive: marked on EVERY loop iteration, including an empty
#   accept()-timeout with no client. This is only for the watchdog below --
#   its job is "is the code itself still cycling, not wedged", which has
#   nothing to do with whether a visitor happened to show up. Using
#   server_alive for the watchdog would reboot the board every ~20s during
#   any normal quiet stretch with nobody looking at the page, which is not
#   a fault.
server_last_alive_ms = None
loop_last_alive_ms = None
_wdt = None


def mark_server_alive():
    global server_last_alive_ms
    server_last_alive_ms = time.ticks_ms()


def server_alive(max_age_ms=15000):
    if server_last_alive_ms is None:
        return False
    return time.ticks_diff(time.ticks_ms(), server_last_alive_ms) < max_age_ms


def mark_loop_alive():
    global loop_last_alive_ms
    loop_last_alive_ms = time.ticks_ms()


def loop_alive(max_age_ms=15000):
    if loop_last_alive_ms is None:
        return False
    return time.ticks_diff(time.ticks_ms(), loop_last_alive_ms) < max_age_ms


def start_watchdog(timeout_ms=20000):
    """Call once the HTTP server is actually listening. From then on, if
    the server loop's heartbeat ever goes stale for the watchdog's full
    timeout, the board hard-resets on its own -- self-healing instead of
    staying silently dead until someone notices. Once created this can't
    be turned off until reboot, which is the point."""
    global _wdt
    _wdt = machine.WDT(timeout=timeout_ms)


def feed_watchdog():
    if _wdt:
        _wdt.feed()


def _sync_time_once():
    global time_known
    try:
        ntptime.settime()
        time_known = True
        print("ntp synced, utc now =", time.localtime())
    except Exception as e:
        print("ntp sync failed, will retry:", e)


def _reset_radio():
    print("wifi stuck, resetting radio interface...")
    try:
        _wlan.active(False)
        time.sleep_ms(500)
        _wlan.active(True)
    except OSError as e:
        print("radio reset raised:", e)


def _retry_forever():
    global connected, rssi
    last_attempt_ms = 0
    was_connected = False
    was_weak = False
    consecutive_failures = 0
    while True:
        if _wlan.isconnected():
            connected = True
            consecutive_failures = 0
            if not was_connected:
                # (re)print the IP on every transition into connected, not
                # just at boot -- DHCP can hand out a different address
                # after a drop, and a stale IP is otherwise indistinguishable
                # from a real outage from the outside.
                print("wifi connected, ip =", _wlan.ifconfig()[0])
                was_connected = True
            try:
                rssi = _wlan.status("rssi")
            except (OSError, ValueError):
                rssi = None
            is_weak = rssi is not None and not signal_ok()
            if is_weak and not was_weak:
                print("wifi signal weak (", rssi, "dBm ) -- requests may be unreliable")
            was_weak = is_weak
            if not time_known:
                _sync_time_once()
        else:
            if was_connected:
                print("wifi dropped, retrying...")
            connected = False
            was_connected = False
            was_weak = False
            rssi = None
            now_ms = time.ticks_ms()
            # Time-gated only -- no attempt to detect "already connecting"
            # via wlan.status(), since that turned out unreliable and was
            # itself causing the re-issued connect() calls that produce
            # "cannot set config" in the log. Patience (the long interval
            # above) is what actually avoids that, not cleverness here.
            if time.ticks_diff(now_ms, last_attempt_ms) > _RETRY_INTERVAL_MS:
                try:
                    _wlan.connect(WIFI_SSID, WIFI_PASSWORD)
                    consecutive_failures = 0
                except OSError as e:
                    print("wifi connect() raised:", e)
                    consecutive_failures += 1
                    if consecutive_failures >= _RADIO_RESET_AFTER_FAILURES:
                        _reset_radio()
                        consecutive_failures = 0
                last_attempt_ms = now_ms
        time.sleep_ms(500)


def start():
    """Bring WLAN up and hand connecting/reconnecting + the one-time NTP
    sync off to a background thread -- boot (and every later reconnect)
    never blocks the tick loop or the HTTP server."""
    global _wlan
    _wlan = network.WLAN(network.STA_IF)
    _wlan.active(False)
    time.sleep(1)
    _wlan.active(True)
    print("mac:", ubinascii.hexlify(_wlan.config("mac"), ":").decode())
    print("scanning...")
    for net in _wlan.scan():
        print("  seen:", repr(net[0].decode()), "rssi:", net[3])
    _thread.start_new_thread(_retry_forever, ())
    return _wlan
