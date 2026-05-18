from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "beacon_crm",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[
        "app.tasks.enrichment",
        "app.tasks.health",
        "app.tasks.email_sync",
        "app.tasks.tldv_sync",
        "app.tasks.crm_import",
        "app.tasks.personal_email_sync",
        "app.tasks.cadence_scheduler",
        "app.tasks.sales_reports",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    # Route user-triggered tasks to priority queue so they're never blocked by long-running syncs
    task_routes={
        "app.tasks.enrichment.process_sourcing_upload_task": {"queue": "priority"},
        "app.tasks.enrichment.icp_research_single_task": {"queue": "priority"},
        "app.tasks.enrichment.icp_research_free_task": {"queue": "priority"},
        "app.tasks.enrichment.icp_research_batch_task": {"queue": "priority"},
    },
    # Daily deal health recalculation at 02:00 UTC
    beat_schedule={
        "recalculate-deal-health-daily": {
            "task": "app.tasks.health.recalculate_all_deal_health",
            "schedule": crontab(hour=2, minute=0),
        },
        "reconcile-recent-deal-tasks": {
            "task": "app.tasks.health.reconcile_recent_deal_tasks",
            # Every 15 min — paired with batch_size=40 in app.tasks.health to
            # keep AI-task lag under ~30 min p95. Hourly was producing >24h
            # lag because throughput (288 deals/day) was below the active
            # deal count.
            "schedule": crontab(minute="2,17,32,47"),
        },
        "sync-gmail-inbox": {
            "task": "app.tasks.email_sync.sync_gmail_inbox",
            "schedule": settings.EMAIL_SYNC_INTERVAL_SECONDS,  # every 3 min
        },
        "sync-tldv-meetings": {
            "task": "app.tasks.tldv_sync.sync_tldv_meetings",
            "schedule": 300,  # every 5 min — matches tldv_sync_interval_minutes default; task self-throttles internally
        },
        "sync-all-personal-inboxes": {
            "task": "app.tasks.personal_email_sync.sync_all_personal_inboxes",
            "schedule": 600,  # every 10 minutes — enqueues one task per connected user
        },
        # Walk each contact's multichannel sequence plan and create tasks for
        # non-email steps when their day_offset has arrived. Runs once every
        # 30 min — tight enough for a call step to appear the same day, loose
        # enough to avoid thrashing the task table.
        "advance-multichannel-cadence": {
            "task": "app.tasks.cadence_scheduler.advance_multichannel_cadence",
            "schedule": 1800,  # every 30 minutes
        },
        "send-us-pod-call-report-daily": {
            "task": "app.tasks.sales_reports.send_us_pod_call_report",
            # Weekdays only. The task sends a weekly report on Friday and skips
            # weekends defensively if beat is ever misconfigured.
            "schedule": crontab(hour=14, minute=30, day_of_week="mon-fri"),
        },
    },
)
