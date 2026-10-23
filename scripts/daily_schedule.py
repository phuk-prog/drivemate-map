"""Select the daily UK 01:00 trigger (02:00 on the spring skipped hour).

GitHub schedules are best effort. Choose by trigger and the UTC calendar date,
not the worker's hour, so ordinary queue delays do not skip an update.
"""
import argparse
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo


def selected_cron(day):
    london = ZoneInfo('Europe/London')
    local = datetime.combine(day, time(1), london).replace(fold=0)
    # On spring-forward day 01:00 does not exist: this maps to the first valid
    # time afterwards, 02:00 BST / 01:00 UTC. Autumn uses the first 01:00 only.
    return '0 ' + str(local.astimezone(timezone.utc).hour) + ' * * *'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--event', required=True)
    parser.add_argument('--cron', default='')
    args = parser.parse_args()
    due = args.event != 'schedule' or args.cron == selected_cron(datetime.now(timezone.utc).date())
    print('due=' + str(due).lower())


if __name__ == '__main__':
    main()
