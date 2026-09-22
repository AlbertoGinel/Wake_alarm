import json
import socket
import time
import _thread

import hardware
import netstatus
import states
import storage
import tzutil

memory = storage.load()


INDEX_HTML = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Alarm - Hardware Test</title>
  <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
  <style>
    :root {
      --bg: #f3f5f9;
      --surface: #ffffff;
      --border: #e2e7ee;
      --text: #1b2430;
      --text-dim: #66707c;
      --accent: #2f6fed;
      --good: #1f8a4c;
      --bad: #c62839;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #11151b;
        --surface: #1a2029;
        --border: #2a3240;
        --text: #e7ecf2;
        --text-dim: #9aa5b2;
        --accent: #5b9bff;
        --good: #3fcf7a;
        --bad: #ff6b76;
      }
    }
    * { box-sizing: border-box; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
      margin: 0;
      padding: 22px 14px 60px;
    }
    #app {
      max-width: 480px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 16px;
    }
    h1 {
      font-size: 1.25rem;
      text-align: center;
      margin: 2px 0 0;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 16px 18px;
    }
    .card h2 {
      margin: 0 0 10px;
      font-size: 0.85rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
    }
    .card p { margin: 6px 0; line-height: 1.5; }
    .hint { font-size: 0.82rem; color: var(--text-dim); }
    pre {
      text-align: left;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px;
      overflow-x: auto;
      font-size: 0.8rem;
      margin: 8px 0;
    }
    table { width: 100%; border-collapse: collapse; margin: 8px 0; }
    table td { padding: 7px 4px; border-bottom: 1px solid var(--border); }
    table tr:last-child td { border-bottom: none; }
    table td:first-child { color: var(--text-dim); }
    table td:last-child { text-align: right; }
    table td[colspan] {
      color: var(--text-dim);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding-top: 14px;
    }
    table tr:first-child td[colspan] { padding-top: 4px; }
    button {
      font-size: 0.92rem;
      font-weight: 600;
      padding: 10px 16px;
      margin: 4px 6px 4px 0;
      border-radius: 8px;
      border: 1px solid var(--accent);
      background: var(--accent);
      color: #fff;
      cursor: pointer;
    }
    button.danger { background: var(--bad); border-color: var(--bad); }
    input[type=number], select {
      font-size: 0.92rem;
      padding: 6px 8px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--bg);
      color: var(--text);
      width: 6em;
    }
    select { width: auto; max-width: 100%; }
    label.checkbox {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin: 4px 10px 4px 0;
      font-size: 0.92rem;
    }
    .pressed { color: var(--good); font-weight: bold; }
    .released { color: var(--text-dim); }
  </style>
</head>
<body>
  <div id="app">
    <h1>ESP32 Hardware Test</h1>

    <section class="card">
      <h2>Network</h2>
      <p :style="{color: networkColor, fontWeight: 'bold'}">{{ networkLabel }}</p>
      <p class="hint">
        webapp reachable (this is the light's ONLY rule): {{ network.serverAlive ? 'yes' : 'no' }}
      </p>
      <p class="hint" style="font-size:0.72rem">
        fyi only, not part of the decision above --
        wifi associated: {{ network.connected ? 'yes' : 'no' }},
        signal: {{ network.rssi !== null && network.rssi !== undefined ? network.rssi + 'dBm' : '?' }},
        time known: {{ network.timeKnown ? 'yes' : 'no' }}
      </p>
      <p class="hint">
        last interaction with the board: {{ secondsSinceLastSeen !== null ? secondsSinceLastSeen + 's ago' : 'never' }}
      </p>
    </section>

    <section class="card">
      <h2>ESP32 Clock</h2>
      <p>{{ clock.iso || '(not loaded)' }} {{ clock.timezone }}</p>
      <p class="hint">epoch (UTC): {{ clock.epochUtc }}</p>
    </section>

    <section class="card">
      <h2>Alarm State Machine</h2>
      <pre>{{ JSON.stringify(status, null, 2) }}</pre>
      <p>
        Load test alarm in
        <input type="number" v-model.number="testLoadInSeconds">
        seconds
        <button @click="testLoadAlarm">Load</button>
        <button @click="forceReset" class="danger">Force Reset (dev)</button>
      </p>
      <p class="hint">
        "Force Reset" always works, from any state -- dev-only escape hatch,
        won't exist in the final product.
      </p>
      <p class="hint">
        Load always spends a few seconds in Loaded, then the rest of the
        window in Before, regardless of the real timeBeforeSec in Config --
        so even a short test always exercises both states.
      </p>
    </section>

    <section class="card">
      <h2>Config</h2>
      <pre>{{ JSON.stringify({...configCfg, buzzer: undefined}, null, 2) }}</pre>
      <table>
        <tr><td colspan="2">Paces</td></tr>
        <tr><td>bufferDownPace</td><td><input type="number" step="0.1" v-model.number="configCfg.bufferDownPace"></td></tr>
        <tr><td>agroUpPace</td><td><input type="number" step="0.1" v-model.number="configCfg.agroUpPace"></td></tr>
        <tr><td>agroDownPace</td><td><input type="number" step="0.1" v-model.number="configCfg.agroDownPace"></td></tr>
        <tr><td>bufferUpPace</td><td><input type="number" step="0.1" v-model.number="configCfg.bufferUpPace"></td></tr>
        <tr><td colspan="2">Timing</td></tr>
        <tr><td>tempIntervalSec</td><td><input type="number" v-model.number="configCfg.tempIntervalSec"></td></tr>
        <tr><td>timeBeforeSec</td><td><input type="number" v-model.number="configCfg.timeBeforeSec"></td></tr>
        <tr><td>winDurationSec</td><td><input type="number" v-model.number="configCfg.winDurationSec"></td></tr>
      </table>
      <button @click="saveConfig">Save All</button>
      <p class="hint">{{ configStatus }}</p>
    </section>

    <section class="card">
      <h2>Switch</h2>
      <p :class="switchPressed ? 'pressed' : 'released'">
        {{ switchPressed ? 'PRESSED' : 'released' }}
      </p>
      <p class="hint">Bar array also lights up fully on the board itself while held (Idle only) -- no need to watch this page while testing the physical button.</p>
    </section>

    <section class="card">
      <h2>RGB LED</h2>
      <select v-model="selectedColorIdx">
        <option v-for="(c, i) in RGB_COLORS" :value="i">{{ c.name }} ({{ c.r }}/{{ c.g }}/{{ c.b }})</option>
      </select>
      <button @click="setRgb">Set Color</button>
      <p class="hint">{{ rgbStatus }}</p>
      <p class="hint">Holds for 10 seconds, then normal state-machine behavior resumes.</p>
    </section>

    <section class="card">
      <h2>Bar Array (7 LEDs)</h2>
      <p>
        <label v-for="(v, i) in barState" :key="i" class="checkbox">
          <input type="checkbox" v-model="barState[i]"> {{ i + 1 }}
        </label>
      </p>
      <button @click="setBar">Set Bar</button>
      <p class="hint">{{ barStatus }}</p>
    </section>

    <section class="card">
      <h2>Buzzer Curve</h2>
      <table>
        <tr><td>minFreqHz</td><td><input type="number" v-model.number="configCfg.buzzer.minFreqHz"></td></tr>
        <tr><td>maxFreqHz</td><td><input type="number" v-model.number="configCfg.buzzer.maxFreqHz"></td></tr>
        <tr><td>minDutyPct</td><td><input type="number" v-model.number="configCfg.buzzer.minDutyPct"></td></tr>
        <tr><td>maxDutyPct</td><td><input type="number" v-model.number="configCfg.buzzer.maxDutyPct"></td></tr>
        <tr><td>beepDurationMs</td><td><input type="number" v-model.number="configCfg.buzzer.beepDurationMs"></td></tr>
        <tr><td>beepPeriodSec</td><td><input type="number" step="0.1" v-model.number="configCfg.buzzer.beepPeriodSec"></td></tr>
      </table>
      <button @click="beep">Beep (tests maxFreqHz/maxDutyPct above)</button>
      <button @click="saveBuzzerCfg">Save Buzzer</button>
      <p class="hint">{{ buzzerStatus }} {{ buzzerCfgStatus }}</p>
    </section>
  </div>

  <script>
    const { createApp, ref, computed, onMounted, onUnmounted } = Vue;

    const RGB_COLORS = [
      { name: 'Black (Off)', r: 0, g: 0, b: 0 },
      { name: 'Very Dark Blue', r: 0, g: 0, b: 50 },
      { name: 'Blue', r: 0, g: 0, b: 100 },
      { name: 'Very Dark Green', r: 0, g: 50, b: 0 },
      { name: 'Dark Cyan', r: 0, g: 50, b: 50 },
      { name: 'Blue-ish Cyan', r: 0, g: 50, b: 100 },
      { name: 'Green', r: 0, g: 100, b: 0 },
      { name: 'Light Green / Spring Green', r: 0, g: 100, b: 50 },
      { name: 'Cyan', r: 0, g: 100, b: 100 },
      { name: 'Very Dark Red', r: 50, g: 0, b: 0 },
      { name: 'Dark Purple / Magenta-ish', r: 50, g: 0, b: 50 },
      { name: 'Purple / Violet', r: 50, g: 0, b: 100 },
      { name: 'Dark Yellow / Olive', r: 50, g: 50, b: 0 },
      { name: 'Gray (Medium)', r: 50, g: 50, b: 50 },
      { name: 'Light Purple / Lavender', r: 50, g: 50, b: 100 },
      { name: 'Yellow-Green', r: 50, g: 100, b: 0 },
      { name: 'Light Green', r: 50, g: 100, b: 50 },
      { name: 'Light Cyan', r: 50, g: 100, b: 100 },
      { name: 'Red', r: 100, g: 0, b: 0 },
      { name: 'Rose / Pinkish Red', r: 100, g: 0, b: 50 },
      { name: 'Magenta', r: 100, g: 0, b: 100 },
      { name: 'Orange', r: 100, g: 50, b: 0 },
      { name: 'Salmon / Coral', r: 100, g: 50, b: 50 },
      { name: 'Hot Pink / Pink', r: 100, g: 50, b: 100 },
      { name: 'Yellow', r: 100, g: 100, b: 0 },
      { name: 'Lime / Yellow-ish', r: 100, g: 100, b: 50 },
      { name: 'White', r: 100, g: 100, b: 100 },
    ];

    createApp({
      setup() {
        const switchPressed = ref(false);
        const selectedColorIdx = ref(0);
        const rgbStatus = ref('(not set)');
        const barState = ref([false, false, false, false, false, false, false]);
        const barStatus = ref('(not set)');
        const buzzerStatus = ref('(not beeped)');
        const clock = ref({ epochUtc: null, iso: '', timezone: '' });
        const status = ref({ state: '?', buffer: 0, agro: 0, holdRemainingSec: 0,
                              wakeUpTimeEpoch: null });
        const configCfg = ref({
          bufferDownPace: 1, agroUpPace: 1, agroDownPace: 2, bufferUpPace: 1.5,
          tempIntervalSec: 120, timeBeforeSec: 3600, winDurationSec: 3,
          buzzer: { minFreqHz: 400, maxFreqHz: 2000, minDutyPct: 1, maxDutyPct: 50,
                    beepDurationMs: 150, beepPeriodSec: 2.0 },
        });
        const configStatus = ref('(not loaded)');
        const buzzerCfgStatus = ref('');
        const testLoadInSeconds = ref(30);
        const network = ref({ connected: false, timeKnown: false, serverAlive: false, rssi: null });
        // One rule, matching states.py: has a real client reached the
        // board in the last ~15s. WiFi-associated-but-nobody's-reached-me
        // is deliberately not a separate case here anymore -- it was
        // redundant (a real client reaching us already implies WiFi
        // works) and just added another thing to distrust.
        const networkOk = () => network.value.serverAlive;
        const networkColor = computed(() => {
          if (!network.value.timeKnown) return '#c00';
          if (!networkOk()) return '#c90';
          return '#06c';
        });
        const networkLabel = computed(() => {
          if (!network.value.timeKnown) return 'RED -- waiting for time sync';
          if (!networkOk()) return 'YELLOW -- not reachable';
          return 'BLUE -- all good';
        });
        const lastSeenMs = ref(null);
        const secondsSinceLastSeen = ref(null);
        let switchPoller = null;
        let statusPoller = null;
        let clockPoller = null;
        let networkPoller = null;
        let lastSeenTicker = null;

        // Every request gets a hard timeout -- a plain fetch() with no
        // timeout can hang for a minute or more on a bad connection (the
        // OS TCP stack retries for a long time before giving up), which is
        // exactly why a single beep used to look like it "did nothing":
        // it was still silently in flight. No auto-retry on top of this on
        // purpose -- a failed action just reports failure once and stops;
        // the human decides whether to click again or move closer to the
        // router.
        const REQUEST_TIMEOUT_MS = 4000;

        async function fetchWithTimeout(url, opts) {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
          try {
            return await fetch(url, { ...opts, signal: controller.signal });
          } finally {
            clearTimeout(timer);
          }
        }

        function describeFailure(e) {
          return e.name === 'AbortError'
            ? 'FAILED: board did not respond in ' + (REQUEST_TIMEOUT_MS / 1000) + 's (check WiFi / get closer to the router)'
            : 'FAILED: ' + e.message;
        }

        async function pollClock() {
          try {
            const res = await fetchWithTimeout('/api/clock');
            clock.value = await res.json();
          } catch (e) {
            // transient network hiccup, next poll will retry
          }
        }

        async function pollNetwork() {
          try {
            const res = await fetchWithTimeout('/api/network');
            network.value = await res.json();
            lastSeenMs.value = Date.now();
          } catch (e) {
            // if this fetch itself fails or times out, we're clearly not
            // reachable -- reflect that instead of leaving the last
            // (stale) good value on screen, that's the whole point of
            // this panel
            network.value = { connected: false, timeKnown: network.value.timeKnown, serverAlive: false, rssi: null };
          }
        }

        async function loadConfig() {
          try {
            const res = await fetchWithTimeout('/api/config');
            configCfg.value = await res.json();
            configStatus.value = 'loaded';
          } catch (e) {
            configStatus.value = describeFailure(e);
          }
        }

        async function saveConfig() {
          configStatus.value = 'saving...';
          try {
            await fetchWithTimeout('/api/config', {
              method: 'POST',
              body: JSON.stringify(configCfg.value),
            });
            configStatus.value = 'saved';
          } catch (e) {
            configStatus.value = describeFailure(e);
          }
        }

        async function saveBuzzerCfg() {
          buzzerCfgStatus.value = 'saving...';
          try {
            await fetchWithTimeout('/api/config', {
              method: 'POST',
              body: JSON.stringify({ buzzer: configCfg.value.buzzer }),
            });
            buzzerCfgStatus.value = 'saved';
          } catch (e) {
            buzzerCfgStatus.value = describeFailure(e);
          }
        }

        async function pollSwitch() {
          try {
            const res = await fetchWithTimeout('/api/switch');
            const data = await res.json();
            switchPressed.value = data.pressed;
          } catch (e) {
            // transient network hiccup, next poll will retry
          }
        }

        async function pollStatus() {
          try {
            const res = await fetchWithTimeout('/api/status');
            status.value = await res.json();
          } catch (e) {
            // transient network hiccup, next poll will retry
          }
        }

        async function setRgb() {
          const c = RGB_COLORS[selectedColorIdx.value];
          rgbStatus.value = 'sending...';
          try {
            const res = await fetchWithTimeout('/api/rgb', {
              method: 'POST',
              body: JSON.stringify({ r: c.r, g: c.g, b: c.b }),
            });
            const data = await res.json();
            rgbStatus.value = JSON.stringify(data);
          } catch (e) {
            rgbStatus.value = describeFailure(e);
          }
        }

        async function setBar() {
          barStatus.value = 'sending...';
          try {
            const res = await fetchWithTimeout('/api/bar', {
              method: 'POST',
              body: JSON.stringify({ states: barState.value }),
            });
            const data = await res.json();
            barStatus.value = JSON.stringify(data);
          } catch (e) {
            barStatus.value = describeFailure(e);
          }
        }

        async function beep() {
          const b = configCfg.value.buzzer;
          buzzerStatus.value = 'sending...';
          try {
            const res = await fetchWithTimeout('/api/buzzer/beep', {
              method: 'POST',
              body: JSON.stringify({ freq: b.maxFreqHz, dutyPct: b.maxDutyPct }),
            });
            const data = await res.json();
            buzzerStatus.value = JSON.stringify(data);
          } catch (e) {
            buzzerStatus.value = describeFailure(e);
          }
        }

        async function testLoadAlarm() {
          try {
            await fetchWithTimeout('/api/alarm/test_load', {
              method: 'POST',
              body: JSON.stringify({ inSeconds: testLoadInSeconds.value }),
            });
          } catch (e) {
            configStatus.value = describeFailure(e);
          }
          pollStatus();
        }

        async function forceReset() {
          try {
            await fetchWithTimeout('/api/reset', { method: 'POST' });
          } catch (e) {
            configStatus.value = describeFailure(e);
          }
          pollStatus();
        }

        onMounted(() => {
          pollSwitch();
          switchPoller = setInterval(pollSwitch, 500);
          pollStatus();
          statusPoller = setInterval(pollStatus, 500);
          pollClock();
          clockPoller = setInterval(pollClock, 1000);
          pollNetwork();
          networkPoller = setInterval(pollNetwork, 2000);
          lastSeenTicker = setInterval(() => {
            secondsSinceLastSeen.value = lastSeenMs.value === null
              ? null : Math.floor((Date.now() - lastSeenMs.value) / 1000);
          }, 1000);
          loadConfig();
        });
        onUnmounted(() => {
          clearInterval(switchPoller);
          clearInterval(statusPoller);
          clearInterval(clockPoller);
          clearInterval(networkPoller);
          clearInterval(lastSeenTicker);
        });

        return {
          switchPressed, buzzerStatus, beep,
          RGB_COLORS, selectedColorIdx, rgbStatus, setRgb,
          barState, barStatus, setBar,
          clock, status, testLoadInSeconds, testLoadAlarm, forceReset,
          configCfg, configStatus, saveConfig,
          buzzerCfgStatus, saveBuzzerCfg,
          network, networkColor, networkLabel, secondsSinceLastSeen,
        };
      }
    }).mount('#app');
  </script>
</body>
</html>
"""

STATUS_TEXT = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
               500: "Internal Server Error"}


def http_response(body, status=200, content_type="text/html"):
    body_bytes = body.encode() if isinstance(body, str) else body
    reason = STATUS_TEXT.get(status, "OK")
    header = (
        "HTTP/1.0 {status} {reason}\r\n"
        "Content-Type: {content_type}\r\n"
        "Content-Length: {length}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status=status, reason=reason, content_type=content_type,
              length=len(body_bytes))
    return header.encode() + body_bytes


def json_response(data, status=200):
    return http_response(json.dumps(data), status=status,
                          content_type="application/json")


def handle_request(method, path, body):
    if path == "/":
        return http_response(INDEX_HTML)
    if path == "/api/ping":
        return json_response({"status": "ok", "uptime_ms": time.ticks_ms()})
    if path == "/api/switch":
        return json_response({"pressed": hardware.switch.value() == 0})
    if path == "/api/rgb" and method == "POST":
        # transient test control only -- never touches memory, nothing saved
        try:
            params = json.loads(body)
        except ValueError:
            return json_response({"error": "invalid json"}, status=400)
        r, g, b = params.get("r", 0), params.get("g", 0), params.get("b", 0)
        # holds for states.RGB_TEST_OVERRIDE_MS regardless of what the
        # current alarm state would otherwise drive the light to
        states.set_rgb_test_override(r, g, b)
        return json_response({"r": r, "g": g, "b": b})
    if path == "/api/bar" and method == "POST":
        # transient test control only -- never touches memory, nothing saved
        try:
            params = json.loads(body)
        except ValueError:
            return json_response({"error": "invalid json"}, status=400)
        bar_states = params.get("states", [])
        hardware.set_bar(bar_states)
        return json_response({"states": bar_states})
    if path == "/api/buzzer/beep":
        # freq/duty are transient test params only -- never touches
        # memory["config"], nothing is saved
        freq, duty_pct = 2000, 50
        if method == "POST" and body:
            try:
                params = json.loads(body)
                freq = params.get("freq", freq)
                duty_pct = params.get("dutyPct", duty_pct)
            except ValueError:
                return json_response({"error": "invalid json"}, status=400)
        hardware.beep(freq=freq, duty_u16=int(duty_pct / 100 * 65535))
        return json_response({"status": "beeped", "freq": freq, "dutyPct": duty_pct})

    if path == "/api/config" and method == "GET":
        return json_response(memory["config"])
    if path == "/api/config" and method == "POST":
        # TODO: re-add `if states.is_locked(memory): return 403` before the
        # real app ships -- unlocked for now so paces can be live-tuned
        # while an alarm is actually running, which is exactly when you
        # want to hear the effect of a change
        try:
            updates = json.loads(body)
        except ValueError:
            return json_response({"error": "invalid json"}, status=400)
        buzzer_updates = updates.pop("buzzer", None)
        memory["config"].update(updates)
        if buzzer_updates:
            memory["config"]["buzzer"].update(buzzer_updates)
        storage.save(memory)
        return json_response(memory["config"])
    if path == "/api/config/buzzer" and method == "POST":
        # calibration only, deliberately not lock-gated
        try:
            updates = json.loads(body)
        except ValueError:
            return json_response({"error": "invalid json"}, status=400)
        memory["config"]["buzzer"].update(updates)
        storage.save(memory)
        return json_response(memory["config"]["buzzer"])

    if path == "/api/network" and method == "GET":
        return json_response({
            "connected": netstatus.connected,
            "timeKnown": netstatus.time_known,
            "serverAlive": netstatus.server_alive(),
            "rssi": netstatus.rssi,  # informational only, doesn't affect the light
        })

    if path == "/api/clock" and method == "GET":
        now = time.time()
        tz_cfg = memory["config"]["timezone"]
        offset_min = tzutil.local_offset_minutes(now, tz_cfg)
        y, mo, d, h, mi, s, _, _ = time.localtime(now + offset_min * 60)
        iso = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(y, mo, d, h, mi, s)
        tz_label = "UTC+{}".format(offset_min // 60)  # Tallinn offsets are always whole hours
        return json_response({"epochUtc": now, "iso": iso, "timezone": tz_label})

    if path == "/api/status" and method == "GET":
        return json_response(memory["session"])
    if path == "/api/reset" and method == "POST":
        # dev-only: force back to Idle from ANY state, bypassing the lock --
        # see states.force_idle for why a physical reset alone can't do this
        states.force_idle(memory)
        return json_response(memory["session"])
    if path == "/api/alarm" and method == "POST":
        try:
            wake_up_epoch = json.loads(body)["wakeUpTimeEpoch"]
        except (ValueError, KeyError):
            return json_response({"error": "invalid json"}, status=400)
        try:
            states.load_alarm(memory, wake_up_epoch)
        except ValueError as e:
            return json_response({"error": str(e)}, status=403)
        return json_response(memory["session"])
    if path == "/api/alarm/test_load" and method == "POST":
        try:
            in_seconds = json.loads(body)["inSeconds"]
        except (ValueError, KeyError):
            return json_response({"error": "invalid json"}, status=400)
        try:
            states.load_alarm(memory, time.time() + in_seconds,
                               before_sec=states.test_before_sec(in_seconds))
        except ValueError as e:
            return json_response({"error": str(e)}, status=403)
        return json_response(memory["session"])
    if path == "/api/alarm/cancel" and method == "POST":
        try:
            states.cancel_to_idle(memory)
        except ValueError as e:
            return json_response({"error": str(e)}, status=403)
        return json_response(memory["session"])

    return json_response({"error": "not found"}, status=404)


def _tick_forever():
    # Runs on the ESP32's second core, independent of whatever the HTTP
    # loop below is doing -- the buzzer/state timing must stay smooth
    # regardless of webpage traffic, so it can no longer share a thread
    # with anything that blocks on network I/O. Only feeds the watchdog
    # while the HTTP loop has also proven itself alive recently -- this
    # loop staying healthy on its own isn't enough, since the HTTP loop
    # can wedge independently. Deliberately uses loop_alive (any completed
    # iteration), not server_alive (a real client reached us) -- the
    # watchdog's job is "is the code stuck", not "has anyone visited", and
    # using server_alive here would reboot the board every ~20s during any
    # normal quiet stretch with no visitors.
    while True:
        states.tick(memory)
        if netstatus.loop_alive():
            netstatus.feed_watchdog()
        time.sleep_ms(20)


def run(host="0.0.0.0"):
    _thread.start_new_thread(_tick_forever, ())

    addr = socket.getaddrinfo(host, 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(5)
    # Wake up periodically even with nobody visiting, so the heartbeat
    # below proves the loop is actually still cycling rather than just
    # reflecting whenever someone last happened to poll it -- otherwise a
    # quiet overnight stretch with zero visitors would look identical to a
    # wedged server.
    s.settimeout(10)
    print("listening on", addr)
    netstatus.start_watchdog()

    while True:
        conn = None
        try:
            conn, client_addr = s.accept()
        except OSError:
            # accept() timing out with no pending connection -- proves
            # nothing either way about a real client (could mean nobody's
            # tried, could mean everyone who tried failed to even reach
            # us), so this deliberately does NOT count as proof the
            # server is reachable. It DOES prove the loop itself is still
            # cycling though, which is all the watchdog cares about.
            netstatus.mark_loop_alive()
            continue

        # A real client reached us at the TCP level -- that's genuine
        # proof of communication, mark both regardless of what happens to
        # this specific request below.
        netstatus.mark_server_alive()
        netstatus.mark_loop_alive()
        try:
            conn.settimeout(2)
            request = conn.recv(1024).decode()
            if not request:
                # client opened a connection and closed it without sending
                # anything -- happens naturally on flaky WiFi, nothing to
                # parse or respond to
                continue
            head, _, body = request.partition("\r\n\r\n")
            request_line = head.split("\r\n", 1)[0]
            parts = request_line.split(" ")
            if len(parts) < 2:
                conn.send(http_response("bad request", status=400, content_type="text/plain"))
                continue
            method, path = parts[0], parts[1]
            path = path.split("?")[0]
            conn.send(handle_request(method, path, body))
        except OSError as e:
            print("connection error:", e)
        except Exception as e:
            print("request error:", e)
        finally:
            if conn:
                conn.close()
