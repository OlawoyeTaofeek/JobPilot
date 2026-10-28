"""APScheduler process (docker-compose 'worker' service):
  - daily 06:00  collector.run_daily
  - daily 09:00  outreach follow-up reminders
  - hourly       expire stale pending approvals
Run: python -m app.workers.scheduler
"""

