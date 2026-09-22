import time

import tzutil

# How many days back the calendar (and the growth calculation below) looks.
LOOKBACK_DAYS = 20

# How many of the most recent *non-neutral* days count toward the hold-time
# growth below -- neutral (unrated) days are skipped entirely rather than
# treated as good or bad.
STREAK_DAYS = 3

# Each "bad" day among those STREAK_DAYS multiplies the hold time by this --
# compounding, so a bad streak grows the hold time rather than just adding
# a flat amount on top. "Good" days don't move it in either direction --
# there's deliberately no way to shrink back below the user's own
# tempIntervalSec, only grow above it.
BAD_DAY_MULTIPLIER = 1.5


def set_rating(memory, date, rating):
    """rating is 'good', 'bad', or None (neutral -- clears the entry)."""
    if rating is None:
        memory["history"].pop(date, None)
    else:
        memory["history"][date] = rating
    _prune(memory)


def _prune(memory):
    # Keep memory.json from growing forever -- nothing outside LOOKBACK_DAYS
    # is ever read anyway.
    cutoff = tzutil.date_str(time.time() - LOOKBACK_DAYS * 86400, memory["config"]["timezone"])
    for date in list(memory["history"]):
        if date < cutoff:
            del memory["history"][date]


def calculated_hold_sec(memory, now_epoch):
    """The user's own tempIntervalSec, grown by BAD_DAY_MULTIPLIER for each
    'bad' day among the most recent STREAK_DAYS non-neutral days (skipping
    neutral days, looking back up to LOOKBACK_DAYS)."""
    config = memory["config"]
    tz_cfg = config["timezone"]
    base = config["tempIntervalSec"]

    bad_count = 0
    checked = 0
    day_epoch = now_epoch
    for _ in range(LOOKBACK_DAYS):
        rating = memory["history"].get(tzutil.date_str(day_epoch, tz_cfg))
        if rating is not None:
            checked += 1
            if rating == "bad":
                bad_count += 1
            if checked >= STREAK_DAYS:
                break
        day_epoch -= 86400

    return base * (BAD_DAY_MULTIPLIER ** bad_count)
