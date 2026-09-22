# Connectivity flags for the BLE transport (ble_service.py), read by
# states.py to drive Idle/Before's status light. Deliberately much simpler
# than netstatus.py's WiFi equivalent: BLE's own connection state IS the
# ground truth the instant it changes (IRQ-driven), so there's no need for
# the heartbeat/staleness-window machinery netstatus.py needed to guess
# whether an HTTP server loop was actually still alive.

# True the instant a BLE central (the phone) is connected, false the
# instant it disconnects -- set directly from ble_service's IRQ handler.
connected = False

# True once the phone has pushed its clock over BLE at least once since
# boot. Never reset back to False afterwards: there's no RTC battery, so
# time.time() is meaningless before that first push, but once seeded it
# keeps ticking correctly on its own even through later disconnects.
time_known = False
