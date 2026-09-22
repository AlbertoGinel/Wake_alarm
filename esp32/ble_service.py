import bluetooth
import json
import machine
import time
import _thread

import hardware
import history
import linkstatus
import states
import storage

_IRQ_CENTRAL_CONNECT = 1
_IRQ_CENTRAL_DISCONNECT = 2
_IRQ_GATTS_WRITE = 3

_FLAG_READ = bluetooth.FLAG_READ
_FLAG_WRITE = bluetooth.FLAG_WRITE
_FLAG_NOTIFY = bluetooth.FLAG_NOTIFY

# Custom 128-bit UUIDs for this project -- not a reused standard service
# (e.g. Nordic UART), just a private block so it can't be confused with
# something a generic BLE app already knows how to talk to.
_SERVICE_UUID = bluetooth.UUID("7a0a0001-8d64-4b1a-9e0a-1234567890ab")
_STATUS_UUID = bluetooth.UUID("7a0a0002-8d64-4b1a-9e0a-1234567890ab")
_CONFIG_UUID = bluetooth.UUID("7a0a0003-8d64-4b1a-9e0a-1234567890ab")
_COMMAND_UUID = bluetooth.UUID("7a0a0004-8d64-4b1a-9e0a-1234567890ab")
_HISTORY_UUID = bluetooth.UUID("7a0a0005-8d64-4b1a-9e0a-1234567890ab")

_STATUS_CHAR = (_STATUS_UUID, _FLAG_READ | _FLAG_NOTIFY)
_CONFIG_CHAR = (_CONFIG_UUID, _FLAG_READ | _FLAG_WRITE)
_COMMAND_CHAR = (_COMMAND_UUID, _FLAG_WRITE)
# Read-only -- writes go through _COMMAND_CHAR's "rateDay" action instead of
# a raw blob write, same reasoning as every other mutation in this file.
_HISTORY_CHAR = (_HISTORY_UUID, _FLAG_READ)
_SERVICE = (_SERVICE_UUID, (_STATUS_CHAR, _CONFIG_CHAR, _COMMAND_CHAR, _HISTORY_CHAR))

_DEVICE_NAME = "ESP32-Alarm"

# Status is pushed on every state change immediately, and otherwise at
# most this often -- buffer/agro/holdRemainingSec change every tick, and
# notifying on every single 20ms tick would be pointless BLE traffic for
# values a human is just glancing at.
_STATUS_NOTIFY_INTERVAL_MS = 500

memory = storage.load()
# Every boot (i.e. every time the physical power switch is flipped back on)
# starts fresh in Idle, on purpose -- session.state used to survive a
# reboot so a mid-alarm crash wouldn't lose the schedule, but that also
# meant powering the board back on could resume straight into Action and
# start beeping immediately if it happened to be armed/mid-alarm when it
# lost power. The daily on/off routine (batteries, EN-pin switch) made
# that the more likely case day-to-day, so "always wake up quiet" wins:
# the wake time has to be re-armed after a power cycle, same as the rest
# of the session.
storage.reset_session(memory)
# Diagnostic only -- history has been reported lost after a power cycle
# and the persistence code looks correct on review (reset_session only
# touches memory["session"]), so this proves on the next real test whether
# the data was actually gone at boot (a storage bug) or was fine here and
# the client just failed to display it (a client bug). Safe to remove
# once that's confirmed either way.
print("history at boot:", memory["history"])

_ble = bluetooth.BLE()
_conn_handle = None
_status_handle = None
_config_handle = None
_command_handle = None
_history_handle = None
_wdt = None


def _advertise():
    name_bytes = _DEVICE_NAME.encode()
    uuid_bytes = bytes(_SERVICE_UUID)

    # The service UUID MUST be in the advertising packet itself -- Chrome's
    # requestDevice({filters: [{services: [...]}]}) only matches devices
    # that broadcast it, it doesn't just check after connecting. This was
    # missing before, which is why the picker found nothing from the very
    # first attempt regardless of pairing state.
    adv_data = bytearray()
    adv_data += bytes((2, 0x01, 0x06))  # flags: general discoverable, BR/EDR not supported
    adv_data += bytes((len(uuid_bytes) + 1, 0x07)) + uuid_bytes  # complete list of 128-bit service UUIDs

    # The name goes in the separate scan-response packet instead of here --
    # flags + a 128-bit UUID already use 21 of the 31 bytes a single BLE
    # advertising packet allows, no room left for the name too.
    resp_data = bytearray()
    resp_data += bytes((len(name_bytes) + 1, 0x09)) + name_bytes  # complete local name

    _ble.gap_advertise(100_000, adv_data=adv_data, resp_data=resp_data)


# The phone sends a standard Unix epoch (seconds since 1970-01-01, from JS
# Date.now()), but MicroPython's time.gmtime() interprets its input as
# seconds since 2000-01-01 -- feeding it a raw Unix epoch lands exactly 30
# years in the future (946684800 = the exact gap between those two
# epochs). Once the RTC itself is set correctly here, every other
# time.time() call elsewhere in the codebase is internally consistent
# again, since they all read back through the same (now-correct) RTC.
_UNIX_TO_MICROPYTHON_EPOCH_OFFSET = 946684800


def _set_time(epoch):
    y, mo, d, h, mi, s, wd, _ = time.gmtime(epoch - _UNIX_TO_MICROPYTHON_EPOCH_OFFSET)
    machine.RTC().datetime((y, mo, d, wd + 1, h, mi, s, 0))
    linkstatus.time_known = True
    print("time set from phone, utc now =", time.localtime())


def _config_payload():
    # "lights" isn't exposed in the BLE client's UI yet, and including it
    # pushes the JSON past 512 bytes -- the hard maximum size for a single
    # BLE characteristic value (not an MTU issue; that ceiling can't be
    # raised). Drop it from what's synced over BLE; storage.py's defaults
    # still apply to it as normal, states.py still reads it from
    # memory["config"]["lights"] same as always.
    payload = dict(memory["config"])
    payload.pop("lights", None)
    return payload


def _apply_config_update(raw):
    try:
        updates = json.loads(raw)
    except ValueError:
        print("config write: invalid json")
        return
    buzzer_updates = updates.pop("buzzer", None)
    lights_updates = updates.pop("lights", None)
    memory["config"].update(updates)
    if buzzer_updates:
        memory["config"]["buzzer"].update(buzzer_updates)
    if lights_updates:
        memory["config"]["lights"].update(lights_updates)
    storage.save(memory)
    # keep the characteristic's own stored value in sync so a later
    # reconnect's read reflects what was actually saved, not just the
    # snapshot taken at boot
    _ble.gatts_write(_config_handle, json.dumps(_config_payload()).encode())


def _rate_day(date, rating):
    history.set_rating(memory, date, rating)
    storage.save(memory)
    # keep the characteristic's own stored value in sync so a later
    # reconnect's read reflects what was actually saved, not just the
    # snapshot taken at boot -- same pattern as _apply_config_update
    _ble.gatts_write(_history_handle, json.dumps(memory["history"]).encode())


def _handle_command(raw):
    try:
        cmd = json.loads(raw)
    except ValueError:
        print("command write: invalid json")
        return
    action = cmd.get("cmd")
    try:
        if action == "arm":
            states.load_alarm(memory, cmd["wakeUpTimeEpoch"])
        elif action == "testLoad":
            in_seconds = cmd["inSeconds"]
            states.load_alarm(memory, time.time() + in_seconds,
                               before_sec=states.test_before_sec(in_seconds))
        elif action == "cancel":
            states.cancel_to_idle(memory)
        elif action == "reset":
            states.force_idle(memory)
        elif action == "setTime":
            _set_time(cmd["epoch"])
        elif action == "rateDay":
            _rate_day(cmd["date"], cmd.get("rating"))
        elif action == "rgb":
            # Goes through the test-override (not hardware.set_rgb directly)
            # so it actually holds instead of being overwritten by the
            # current state's own tick handler within ~100ms -- that was
            # the bug: the color changed, but nothing kept it there.
            states.set_rgb_test_override(cmd.get("r", 0), cmd.get("g", 0), cmd.get("b", 0),
                                          duration_ms=2000)
        elif action == "bar":
            states.set_bar_test_override(cmd.get("states", []), duration_ms=2000)
        elif action == "beep":
            freq = cmd.get("freq", 2000)
            duty_pct = cmd.get("dutyPct", 50)
            hardware.beep(freq=freq, duty_u16=int(duty_pct / 100 * 65535))
        elif action == "ir":
            hardware.ir_command(cmd.get("name"))
        elif action == "irLoop":
            hardware.ir_repeat_start(cmd.get("name", "on"), cmd.get("durationMs", 30000))
        else:
            print("unknown command:", action)
    except (ValueError, KeyError) as e:
        print("command error:", e)


def _irq(event, data):
    global _conn_handle
    if event == _IRQ_CENTRAL_CONNECT:
        _conn_handle, _, _ = data
        linkstatus.connected = True
        print("ble central connected")
    elif event == _IRQ_CENTRAL_DISCONNECT:
        _conn_handle = None
        linkstatus.connected = False
        print("ble central disconnected, advertising again")
        _advertise()
    elif event == _IRQ_GATTS_WRITE:
        conn_handle, attr_handle = data
        value = _ble.gatts_read(attr_handle)
        if attr_handle == _config_handle:
            _apply_config_update(value)
        elif attr_handle == _command_handle:
            _handle_command(value)


def _status_payload():
    payload = dict(memory["session"])
    payload["now"] = time.time()
    payload["timeKnown"] = linkstatus.time_known
    # No battery hardware installed yet (see the TP4056 + buck-boost plan
    # discussed separately) -- null is the honest value until that ADC
    # reading actually exists. Client shows "not installed" for null.
    payload["batteryPct"] = None
    return payload


def _send_status(force=False):
    global _last_notify_ms
    if _conn_handle is None:
        return
    payload = json.dumps(_status_payload()).encode()
    _ble.gatts_write(_status_handle, payload)
    if force or time.ticks_diff(time.ticks_ms(), _last_notify_ms) >= _STATUS_NOTIFY_INTERVAL_MS:
        try:
            _ble.gatts_notify(_conn_handle, _status_handle, payload)
        except OSError:
            pass
        _last_notify_ms = time.ticks_ms()


_last_notify_ms = 0


def _tick_forever():
    # Runs on the ESP32's second core, same reasoning as the old HTTP
    # design: BLE IRQ handling and the buzzer/light tick loop must never
    # block each other. No "did a real request reach me" heartbeat
    # gymnastics needed for the watchdog here (unlike the old HTTP
    # version) -- BLE's IRQ-driven model means there's nothing this loop
    # can silently wedge behind, so it just feeds the watchdog every pass.
    last_state = None
    while True:
        try:
            states.tick(memory)
            hardware.ir_repeat_tick()
            hardware.ir_sequence_tick()
            state_changed = memory["session"]["state"] != last_state
            last_state = memory["session"]["state"]
            _send_status(force=state_changed)
        except Exception as e:
            # Without this, any unexpected error here (bad data, an edge
            # case we didn't anticipate) would silently kill this whole
            # thread -- no crash message, nothing -- and ~20s later the
            # watchdog would force a full board reboot because nothing's
            # feeding it anymore. That looked exactly like "BLE
            # disconnected for no reason" from the phone's side, since a
            # full reboot IS a much bigger event than it appeared to be.
            # Catching it here means one bad tick gets logged and skipped
            # instead of taking the whole board down with it.
            print("tick loop error (recovered):", e)
        if _wdt:
            _wdt.feed()
        time.sleep_ms(20)


def start():
    global _status_handle, _config_handle, _command_handle, _history_handle, _wdt

    _ble.active(True)
    try:
        _ble.config(mtu=256)  # room for the config JSON in one write/read
    except OSError:
        pass
    _ble.irq(_irq)
    ((_status_handle, _config_handle, _command_handle, _history_handle),) = \
        _ble.gatts_register_services((_SERVICE,))
    _ble.gatts_write(_config_handle, json.dumps(_config_payload()).encode())
    _ble.gatts_write(_status_handle, json.dumps(_status_payload()).encode())
    _ble.gatts_write(_history_handle, json.dumps(memory["history"]).encode())
    # Command is write-only, so it never otherwise gets a server-side write
    # to establish its buffer size from -- without this, MicroPython sizes
    # it off the first (small) client write and silently truncates every
    # command after that. 256 bytes is generous headroom for the longest
    # command we send (the 7-value "bar" array).
    _ble.gatts_write(_command_handle, bytes(256))
    _advertise()
    print("ble advertising as", _DEVICE_NAME)

    _wdt = machine.WDT(timeout=20000)
    _thread.start_new_thread(_tick_forever, ())
