import json
import os

MEMORY_PATH = "memory.json"

DEFAULT_CONFIG = {
    "timeBeforeSec": 3600,
    "bufferDownPace": 1.0,
    "agroUpPace": 1.0,
    "agroDownPace": 2.0,
    "bufferUpPace": 1.5,
    "tempIntervalSec": 120,
    "winDurationSec": 3,
    "timezone": {
        # Tallinn: EET (UTC+2) standard, EEST (UTC+3) during EU summer time
        "stdOffsetMinutes": 120,
        "dstOffsetMinutes": 180,
        "dstRule": "eu",  # last Sunday of March 01:00 UTC -> last Sunday of October 01:00 UTC
    },
    "buzzer": {
        "minFreqHz": 400,
        "maxFreqHz": 2000,
        # duty is the real loudness dial on a bare piezo: 50% is the
        # physical peak, so the curve runs from far-off-peak (weak) up to
        # exactly 50% (loudest) rather than a naive 0-100% duty sweep
        "minDutyPct": 1,
        "maxDutyPct": 50,
        "curve": "linear",
        "beepDurationMs": 150,
        "beepPeriodSec": 2.0,
    },
    "lights": {
        "idleFadePeriodSec": 6.0,
        "loadedBlinkPeriodSec": 0.25,
        "loadedBlinkCount": 3,
        # Time for one full hue rotation of Before's rainbow -- "slow" is
        # the point, this isn't meant to be an alert, just a nice colorful
        # effect while waiting.
        "beforeRainbowPeriodSec": 10.0,
        # Action's RGB blink speeds up as buffer empties / agro climbs, same
        # idea as the buzzer's beep-rate curve -- min/max bound the period.
        "actionBlinkMinPeriodSec": 0.15,
        "actionBlinkMaxPeriodSec": 1.0,
        "winBlinkPeriodSec": 0.3,
        "winFlickerPeriodSec": 0.08,
        # [freqHz, durationMs] pairs played back to back on Win, looping
        # silently once done if winDurationSec runs longer than the melody.
        "winMelody": [
            [523, 150], [659, 150], [784, 150],
            [1047, 300], [784, 100], [1047, 500],
        ],
        "winMelodyDutyPct": 50,
    },
}

DEFAULT_SESSION = {
    "state": "idle",
    "wakeUpTimeEpoch": None,
    "buffer": 100,
    "agro": 0,
    # live countdown while holding the button -- initialized from
    # config.tempIntervalSec at Load, the ONLY "tempInterval*" name in the
    # whole system; this is deliberately a different name so it can never
    # be confused with (or accidentally overwrite) that config setting
    "holdRemainingSec": DEFAULT_CONFIG["tempIntervalSec"],
    # The actual hold duration required for the current armed instance --
    # holdRemainingSec's starting point and Button's countdown denominator.
    # Usually equals config.tempIntervalSec, but grows above it if recent
    # days were rated "bad" (see history.calculated_hold_sec).
    "holdTotalSec": DEFAULT_CONFIG["tempIntervalSec"],
    # Before's actual lead time for the current armed instance -- normally
    # a copy of config["timeBeforeSec"], but a test-loaded alarm overrides
    # it with a short value (see states.load_alarm/test_before_sec).
    "beforeSec": DEFAULT_CONFIG["timeBeforeSec"],
}


def _merge_defaults(target, defaults):
    for key, value in defaults.items():
        if key not in target:
            target[key] = value
        elif isinstance(value, dict) and isinstance(target[key], dict):
            _merge_defaults(target[key], value)
    return target


def load():
    try:
        with open(MEMORY_PATH) as f:
            memory = json.loads(f.read())
    except (OSError, ValueError):
        memory = {}
    memory.setdefault("config", {})
    memory.setdefault("session", {})
    memory.setdefault("history", {})  # date string ("YYYY-MM-DD") -> "good"/"bad"
    _merge_defaults(memory["config"], DEFAULT_CONFIG)
    _merge_defaults(memory["session"], DEFAULT_SESSION)
    return memory


def save(memory):
    # write-to-temp-then-rename so a power drop mid-write can't corrupt
    # the file the ESP32 depends on to recover its schedule after a reboot
    tmp_path = MEMORY_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        f.write(json.dumps(memory))
    os.rename(tmp_path, MEMORY_PATH)


def reset_session(memory):
    memory["session"] = dict(DEFAULT_SESSION)
    memory["session"]["holdRemainingSec"] = memory["config"]["tempIntervalSec"]
    memory["session"]["holdTotalSec"] = memory["config"]["tempIntervalSec"]
    save(memory)
    return memory
