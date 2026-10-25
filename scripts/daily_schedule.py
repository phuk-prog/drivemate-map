"""Select the twice-daily UK 01:00 and 14:00 triggers (02:00 on the spring skipped hour).

GitHub schedules are best effort. Choose by trigger and the UTC calendar date,
not the worker's hour, so ordinary queue delays do not skip an update. Each
slot has a BST and a GMT cron; exactly one of each pair is due on any date.
"""
import argparse
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo('Europe/London')
SLOTS = (1, 14)


def slot_utc(day, hour):
    # On spring-forward day 01:00 does not exist: this maps to the first valid
    # time afterwards, 02:00 BST / 01:00 UTC. Autumn uses the first 01:00 only.
    local = datetime.combine(day, time(hour), LONDON).replace(fold=0)
    return local.astimezone(timezone.utc)


def selected_crons(day):
    return {'0 ' + str(slot_utc(day, hour).hour) + ' * * *' for hour in SLOTS}


def selected_cron(day):
    """The 01:00 trigger (kept for callers that only need the night update)."""
    return '0 ' + str(slot_utc(day, SLOTS[0]).hour) + ' * * *'


def latest_slot(now):
    """The most recent 01:00 or 14:00 UK update time at or before [now] (UTC)."""
    now = now.astimezone(timezone.utc)
    day = now.astimezone(LONDON).date()
    for back in range(3):
        times = sorted((slot_utc(day - timedelta(days=back), h) for h in SLOTS), reverse=True)
        for t in times:
            if t <= now:
                return t
    raise ValueError('No update slot found')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', required=True)
    parser.add_argument('--cron', default='')
    args = parser.parse_args()
    due = args.event != 'schedule' or args.cron in selected_crons(datetime.now(timezone.utc).date())
    print('due=' + str(due).lower())


if __name__ == '__main__':
    main()
