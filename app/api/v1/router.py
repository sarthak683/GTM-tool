"""
Aggregates all v1 endpoint routers into a single router.

main.py includes this with prefix="/api/v1", so all routes are versioned
automatically. Adding v2 later means creating app/api/v2/router.py and
mounting it without touching any v1 files.
"""
from fastapi import APIRouter

# Kept alphabetical so a new endpoint module has one obvious insertion point and
# a duplicate is visible on sight. `knowledge` and `drive` were each listed twice
# here, and the second `drive` include below registered all 7 of its routes a
# second time.
from app.api.v1.endpoints import (
    account_sourcing,
    activities,
    admin_health,
    aircall,
    analytics,
    angel_mapping,
    assignments,
    auth,
    battlecards,
    call_recordings,
    companies,
    contacts,
    crm_imports,
    custom_demo,
    data_room,
    deals,
    drive,
    email_sync,
    enrichment,
    execution_tracker,
    global_search,
    intelligence,
    knowledge,
    meetings,
    notifications,
    outreach,
    performance,
    personal_email_sync,
    prospecting,
    push,
    reminders,
    sales_reports,
    sales_resources,
    settings,
    signals,
    tasks,
    tldv,
    webhooks,
    workspace,
    zippy,
)

router = APIRouter()

router.include_router(auth.router)
router.include_router(admin_health.router)
router.include_router(assignments.router)
router.include_router(companies.router)
router.include_router(contacts.router)
router.include_router(deals.router)
router.include_router(analytics.router)
router.include_router(performance.router)
router.include_router(crm_imports.router)
router.include_router(execution_tracker.router)
router.include_router(activities.router)
router.include_router(enrichment.router)
router.include_router(global_search.router)
router.include_router(prospecting.router)
router.include_router(outreach.router)
router.include_router(intelligence.router)
router.include_router(signals.router)
router.include_router(tasks.router)
router.include_router(tldv.router)
router.include_router(meetings.router)
router.include_router(battlecards.router)
router.include_router(webhooks.router)
router.include_router(custom_demo.router)
router.include_router(data_room.router)
router.include_router(sales_resources.router)
router.include_router(sales_reports.router)
router.include_router(account_sourcing.router)
router.include_router(angel_mapping.router)
router.include_router(settings.router)
router.include_router(aircall.router)
router.include_router(email_sync.router)
router.include_router(personal_email_sync.router)
router.include_router(drive.router)
router.include_router(push.router)
router.include_router(reminders.router)
router.include_router(call_recordings.router)
router.include_router(notifications.router)
router.include_router(workspace.router)
router.include_router(knowledge.router)
router.include_router(zippy.router)
