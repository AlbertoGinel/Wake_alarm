import time


def _mk(y, mo, d, h=0, mi=0, s=0):
    return time.mktime((y, mo, d, h, mi, s, 0, 0))


def _eu_last_sunday_epoch(year, month, hour_utc):
    """Epoch (UTC) of 01:00 on the last Sunday of the given month --
    the EU's DST transition rule, which Estonia follows."""
    next_month_first = _mk(year + 1, 1, 1) if month == 12 else _mk(year, month + 1, 1)
    last_day = next_month_first - 86400
    weekday = time.localtime(last_day)[6]  # 0=Mon .. 6=Sun
    days_back = (weekday + 1) % 7
    return last_day - days_back * 86400 + hour_utc * 3600


def _eu_dst_active(now_epoch):
    year = time.localtime(now_epoch)[0]
    start = _eu_last_sunday_epoch(year, 3, 1)
    end = _eu_last_sunday_epoch(year, 10, 1)
    return start <= now_epoch < end


def local_offset_minutes(now_epoch, tz_cfg):
    if tz_cfg.get("dstRule") == "eu" and _eu_dst_active(now_epoch):
        return tz_cfg["dstOffsetMinutes"]
    return tz_cfg["stdOffsetMinutes"]


def date_str(epoch, tz_cfg):
    """YYYY-MM-DD for the given UTC epoch, in the local calendar day
    described by tz_cfg (see storage.DEFAULT_CONFIG['timezone'])."""
    offset_min = local_offset_minutes(epoch, tz_cfg)
    y, mo, d, _, _, _, _, _ = time.localtime(epoch + offset_min * 60)
    return "{:04d}-{:02d}-{:02d}".format(y, mo, d)
