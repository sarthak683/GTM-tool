from uuid import uuid4

from app.models.activity import Activity
from app.services.us_pod_call_report import _activity_rep_id


def _rep_id_for(activity: Activity, *, rep_ids: set, owner_id=None, aircall_name_map=None):
    return _activity_rep_id(
        activity,
        rep_ids=rep_ids,
        rep_ids_by_aircall_name=aircall_name_map or {},
        deal_owner={activity.deal_id: owner_id} if activity.deal_id else {},
        contact_owner={activity.contact_id: owner_id} if activity.contact_id else {},
    )


def test_manual_call_outside_report_roster_is_not_credited_to_contact_owner():
    creator_id = uuid4()
    owner_id = uuid4()
    contact_id = uuid4()
    activity = Activity(
        type="call",
        medium="call",
        source="manual",
        contact_id=contact_id,
        created_by_id=creator_id,
    )

    assert _rep_id_for(activity, rep_ids={owner_id}, owner_id=owner_id) is None


def test_manual_call_is_credited_to_explicit_creator_in_report_roster():
    creator_id = uuid4()
    owner_id = uuid4()
    contact_id = uuid4()
    activity = Activity(
        type="call",
        medium="call",
        source="manual",
        contact_id=contact_id,
        created_by_id=creator_id,
    )

    assert _rep_id_for(activity, rep_ids={creator_id, owner_id}, owner_id=owner_id) == creator_id


def test_aircall_without_creator_keeps_owner_fallback():
    owner_id = uuid4()
    contact_id = uuid4()
    activity = Activity(
        type="call",
        medium="call",
        source="aircall",
        contact_id=contact_id,
    )

    assert _rep_id_for(activity, rep_ids={owner_id}, owner_id=owner_id) == owner_id


def test_aircall_agent_name_still_takes_priority_over_owner():
    agent_id = uuid4()
    owner_id = uuid4()
    contact_id = uuid4()
    activity = Activity(
        type="call",
        medium="call",
        source="aircall",
        contact_id=contact_id,
        aircall_user_name="Jacob David Raudy",
    )

    assert (
        _rep_id_for(
            activity,
            rep_ids={agent_id, owner_id},
            owner_id=owner_id,
            aircall_name_map={"jacob david raudy": agent_id},
        )
        == agent_id
    )


def test_aircall_agent_outside_report_roster_is_not_credited_to_owner():
    owner_id = uuid4()
    contact_id = uuid4()
    activity = Activity(
        type="call",
        medium="call",
        source="aircall",
        contact_id=contact_id,
        aircall_user_name="Outside Pod Rep",
    )

    assert _rep_id_for(activity, rep_ids={owner_id}, owner_id=owner_id) is None
