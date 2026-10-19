"""Celery Beat schedule. All times are UTC (celery `timezone="UTC"`).

00:15  daily_cycle        ingest → validate → build_features → predict   (Celery chain)
       (Bitstamp's day closes at 00:00 UTC; 15 minutes of slack for the source to update)
01:00  evaluate_outcomes  score yesterday's forecasts now that their target day has closed
Sun 02:00  weekly_training   train_candidates → promote_model               (Celery chain)

Monitoring (queue `monitoring`):
hh:05  monitor_freshness   hourly: is the newest candle too old?
00:45  monitor_drift       after the daily chain has written today's features + forecast
01:15  monitor_performance after evaluate_outcomes has scored yesterday's forecast

Beat only ENQUEUES; workers execute. Run exactly one beat process, or jobs are enqueued twice
(the Redis locks would then skip the duplicates, but one scheduler is the rule).
"""

from typing import Any

from celery.schedules import crontab

BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    "daily-cycle": {
        "task": "marketlens.daily_cycle",
        "schedule": crontab(hour=0, minute=15),
    },
    "evaluate-outcomes": {
        "task": "marketlens.evaluate_outcomes",
        "schedule": crontab(hour=1, minute=0),
    },
    "weekly-training": {
        "task": "marketlens.weekly_training",
        "schedule": crontab(hour=2, minute=0, day_of_week="sun"),
    },
    "monitor-freshness": {
        "task": "marketlens.monitor_freshness",
        "schedule": crontab(minute=5),
    },
    "monitor-drift": {
        "task": "marketlens.monitor_drift",
        "schedule": crontab(hour=0, minute=45),
    },
    "monitor-performance": {
        "task": "marketlens.monitor_performance",
        "schedule": crontab(hour=1, minute=15),
    },
}
