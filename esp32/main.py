# WiFi/HTTP (netstatus.py + webapp.py) is kept in the repo but is
# deliberately NOT imported here -- switched the primary "configure and
# arm from a phone" path to BLE (ble_service.py) since it doesn't depend
# on the router at all, only on the phone being physically near the
# device, which is already true every time you're actually setting the
# alarm. WiFi code stays latent in case it's wanted again later (accurate
# time via NTP as a fallback, the wifi-bulb integration).
import time

import ble_service
ble_service.start()

# ble_service.start() is non-blocking (IRQ-driven BLE + a background tick
# thread) -- keep main.py itself alive anyway, matching every other entry
# point in this project, rather than relying on letting the script end
# being harmless.
while True:
    time.sleep(3600)
