import type { TaskItem } from "../../types";

type SystemTaskGuidance = {
  intro: string;
  steps: string[];
};

function asString(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

export function getSystemTaskGuidance(task: TaskItem): SystemTaskGuidance | null {
  if (task.task_type !== "system" || !task.recommended_action) {
    return null;
  }

  const payload = task.action_payload ?? {};
  const meetingTitle = asString(payload.meeting_title);
  const followUpDraft = asString(payload.follow_up_email_draft);
  const targetStage = asString(payload.target_stage).replace(/_/g, " ");
  const newValue = typeof payload.new_value === "number" ? payload.new_value : null;
  const currency = asString(payload.currency) || "USD";
  const newCloseDate = asString(payload.new_close_date);
  const medpiccField = asString(payload.field).replace(/_/g, " ");
  const medpiccScore = typeof payload.target_score === "number" ? payload.target_score : null;
  const medpiccSummary = asString(payload.summary);
  const medpiccEvidence = asString(payload.evidence);
  const medpiccChangeReason = asString(payload.change_reason).replace(/_/g, " ");
  const medpiccContact = payload.contact && typeof payload.contact === "object" ? payload.contact as Record<string, unknown> : null;
  const medpiccContactName = asString(medpiccContact?.name);
  const medpiccTags = Array.isArray(payload.tags)
    ? payload.tags.filter((tag): tag is string => typeof tag === "string" && tag.trim().length > 0)
    : [];
  const contactEmail = asString(payload.email);
  const contactChangeType = asString(payload.change_type);
  const contactTitle = asString(payload.title);
  const criticalRule = asString(payload.rule_id).replace(/_/g, " ");
  const criticalSeverity = asString(payload.severity) || "high";

  const byAction: Record<string, SystemTaskGuidance> = {
    move_deal_stage: {
      intro: "If accepted, Beacon will update the deal record for you.",
      steps: [
        "Read the latest buyer signal and confirm the recommended stage change.",
        "Move the deal to the new stage in the pipeline.",
        "Log the stage movement in the deal activity timeline.",
      ],
    },
    convert_contact_to_deal: {
      intro: "If accepted, Beacon will convert this prospect into a tracked deal.",
      steps: [
        "Create a new deal from the accepted prospect.",
        "Carry over the linked company and core context.",
        "Attach the prospect to the new deal so follow-up stays connected.",
      ],
    },
    attach_contact_to_deal: {
      intro: "If accepted, Beacon will clean up stakeholder mapping on this deal.",
      steps: [
        "Find the existing contact Beacon matched from recent activity.",
        "Attach that contact to the deal as a stakeholder.",
        "Update the deal timeline so the team sees the added stakeholder.",
      ],
    },
    create_contact_and_attach_to_deal: {
      intro: "If accepted, Beacon will add the new stakeholder into the CRM for you.",
      steps: [
        "Create a new contact from the detected participant.",
        "Attach the contact to the current deal.",
        "Keep the stakeholder map and activity history aligned.",
      ],
    },
    re_enrich_company: {
      intro: "If accepted, Beacon will refresh the company record in the background.",
      steps: [
        "Queue a fresh company enrichment run.",
        "Pull updated firmographic and research signals into the account.",
        "Leave the enriched data on the company for the team to review.",
      ],
    },
    refresh_icp_research: {
      intro: "If accepted, Beacon will refresh fit and messaging context for this account.",
      steps: [
        "Queue a new ICP research pass for the account.",
        "Re-evaluate fit, timing, and likely outreach angle.",
        "Save the refreshed research back onto the account record.",
      ],
    },
    re_enrich_contact: {
      intro: "If accepted, Beacon will refresh the contact profile in the background.",
      steps: [
        "Queue contact enrichment for the selected stakeholder.",
        "Pull updated role, title, and context signals.",
        "Save the refreshed contact data back into Beacon.",
      ],
    },
    send_pricing_package: {
      intro: "If accepted, Beacon will complete the CRM-side pricing follow-up step.",
      steps: [
        "Use the latest buyer signal to mark the pricing follow-up as handled.",
        "Update the deal's follow-up status in Beacon.",
        "Record the action in the activity timeline so the team has context.",
      ],
    },
    book_workshop_session: {
      intro: "If accepted, Beacon will mark the workshop motion forward in the CRM.",
      steps: [
        "Use the recent buyer signal to advance the workshop follow-up.",
        "Update the relevant deal follow-up state.",
        "Log the workshop action in the activity timeline.",
      ],
    },
    retry_deal_call: {
      intro: "If accepted, Beacon will mark the buyer call retry action as completed in Beacon.",
      steps: [
        "Take the missed-call recommendation forward.",
        "Update the CRM task state to show the retry action was handled.",
        "Leave an activity trail for the team to reference.",
      ],
    },
    follow_up_deal_voicemail: {
      intro: "If accepted, Beacon will record the voicemail follow-up action in the CRM.",
      steps: [
        "Use the voicemail signal to mark the follow-up motion complete.",
        "Update the deal activity record.",
        "Keep the timeline current so the next rep sees the latest action.",
      ],
    },
    send_deal_call_recap: {
      intro: "If accepted, Beacon will record the call recap action on the deal.",
      steps: [
        "Use the recent call context to mark the recap as handled.",
        "Update the deal follow-up state in Beacon.",
        "Write the recap action to the activity timeline.",
      ],
    },
    send_meeting_follow_up: {
      intro: "If accepted, Beacon will use the meeting context Beacon already prepared.",
      steps: [
        meetingTitle ? `Use the summary and notes from ${meetingTitle}.` : "Use the latest meeting summary and transcript context.",
        followUpDraft ? "Apply the drafted follow-up Beacon already prepared." : "Prepare the meeting follow-up from the captured discussion.",
        "Mark the recommendation as handled and record the action in the deal timeline.",
      ],
    },
    follow_up_buyer_thread: {
      intro: "If accepted, Beacon will complete the buyer follow-up step in the CRM.",
      steps: [
        "Use the latest buyer thread context Beacon detected.",
        "Advance the follow-up action for the deal.",
        "Record that movement in the timeline so the team sees it immediately.",
      ],
    },
    retry_contact_call: {
      intro: "If accepted, Beacon will update the prospect call-follow-up action for you.",
      steps: [
        "Mark the recommended retry action as handled.",
        "Update the prospect task state in Beacon.",
        "Leave an audit trail in the activity timeline.",
      ],
    },
    follow_up_voicemail: {
      intro: "If accepted, Beacon will log the voicemail follow-up action in Beacon.",
      steps: [
        "Take the voicemail-based recommendation forward.",
        "Update the prospect follow-up state.",
        "Record the action so the owner has a clean activity trail.",
      ],
    },
    send_contact_call_recap: {
      intro: "If accepted, Beacon will complete the contact recap step in the CRM.",
      steps: [
        "Use the call context already captured.",
        "Mark the recap recommendation as handled.",
        "Write the update into the prospect timeline.",
      ],
    },
    draft_reply_follow_up: {
      intro: "If accepted, Beacon will move the drafted-reply workflow forward in Beacon.",
      steps: [
        "Use the existing conversation context Beacon has on the prospect.",
        "Mark the draft-reply recommendation as handled.",
        "Update the timeline so the next owner sees the motion clearly.",
      ],
    },
    draft_open_follow_up: {
      intro: "If accepted, Beacon will move the open follow-up workflow forward in Beacon.",
      steps: [
        "Use the open thread context Beacon already captured.",
        "Advance the follow-up recommendation in the CRM.",
        "Record the action for clean pipeline visibility.",
      ],
    },
    book_call_from_interest: {
      intro: "If accepted, Beacon will update the prospect state from interest to booked-call motion.",
      steps: [
        "Use the positive buyer signal on the prospect.",
        "Update the contact status to reflect the booked-call intent.",
        "Log the change in Beacon so the team sees the progression.",
      ],
    },
    mark_contact_unsubscribed: {
      intro: "If accepted, Beacon will update the prospect's contactability state.",
      steps: [
        "Mark the contact as unsubscribed in Beacon.",
        "Keep future outreach from re-queuing incorrectly.",
        "Record the hygiene update in the CRM timeline.",
      ],
    },
    close_not_interested_contact: {
      intro: "If accepted, Beacon will close out the prospect as not interested.",
      steps: [
        "Update the prospect status to not interested.",
        "Remove it from the active follow-up queue.",
        "Leave a clear activity record explaining the change.",
      ],
    },
    // ── AI task emitter — the 6 canonical codes ──────────────────────────
    t_stage_apply: {
      intro: targetStage
        ? `If accepted, Beacon will move this deal to "${targetStage}" and stamp the stage change.`
        : "If accepted, Beacon will move this deal to the recommended stage.",
      steps: [
        "Read the buyer evidence Beacon cited and confirm the stage really moved.",
        targetStage ? `Set the deal stage to ${targetStage} and reset days-in-stage.` : "Update the deal stage.",
        "Write a stage_change activity so the pipeline stays auditable.",
      ],
    },
    t_amount_apply: {
      intro: newValue
        ? `If accepted, Beacon will set the deal value to ${currency} ${newValue.toLocaleString()}.`
        : "If accepted, Beacon will update the deal value to match the latest buyer signal.",
      steps: [
        "Check the cited email/call — a number has been proposed, agreed, or discounted.",
        newValue ? `Overwrite the current deal value with ${currency} ${newValue.toLocaleString()}.` : "Update the deal value.",
        "Log the change on the deal timeline for forecast accuracy.",
      ],
    },
    t_close_apply: {
      intro: newCloseDate
        ? `If accepted, Beacon will re-anchor the expected close date to ${newCloseDate}.`
        : "If accepted, Beacon will update the expected close date to match the buyer's stated timeline.",
      steps: [
        "Confirm the prospect named or implied this new timeline.",
        newCloseDate ? `Set expected close to ${newCloseDate}.` : "Update the expected close date.",
        "Record the change so the forecast reflects the newest buyer guidance.",
      ],
    },
    t_medpicc_apply: {
      intro: medpiccField && medpiccScore
        ? `If accepted, Beacon will update MEDDPICC "${medpiccField}" to level ${medpiccScore} and save the new field detail.`
        : "If accepted, Beacon will fill in the MEDDPICC field the latest conversation surfaced.",
      steps: [
        medpiccSummary ? `Write this field summary: ${medpiccSummary}` : "Write the new MEDDPICC detail Beacon extracted from the conversation.",
        medpiccEvidence ? `Evidence: ${medpiccEvidence}` : "Review the evidence Beacon captured from the conversation.",
        medpiccContactName ? `Link the named stakeholder context: ${medpiccContactName}.` : "Carry over any stakeholder context included in the update.",
        medpiccTags.length > 0 ? `Save the supporting tags: ${medpiccTags.join(", ")}.` : "Store any supporting tags or competitor names Beacon captured.",
        medpiccField && medpiccScore
          ? `Set MEDDPICC ${medpiccField} to ${medpiccScore} (1=identified, 2=validated, 3=confirmed).`
          : "Advance the MEDDPICC field to the correct level.",
        medpiccChangeReason ? `Mark this as a ${medpiccChangeReason} update so Beacon can dedupe future prompts.` : "Record why this MEDDPICC field changed.",
        "Recompute the MEDDPICC score on the deal.",
      ],
    },
    t_contact_apply: {
      intro: contactEmail
        ? contactChangeType === "update"
          ? `If accepted, Beacon will update ${contactEmail} on this deal.`
          : `If accepted, Beacon will add ${contactEmail} as a stakeholder on this deal.`
        : "If accepted, Beacon will keep the buying-committee map clean.",
      steps: [
        contactChangeType === "update"
          ? "Patch the existing contact with the newly detected role/title."
          : "Create or link the contact Beacon spotted on the thread.",
        contactTitle ? `Role/title: ${contactTitle}.` : "Attach the stakeholder to this deal.",
        "Leave a contact_linked activity so stakeholder history stays visible.",
      ],
    },
    t_critical_apply: {
      intro: criticalRule
        ? `Critical (${criticalSeverity}): ${criticalRule}. Beacon cannot fix this — a human needs to act.`
        : "Beacon flagged a critical, time-sensitive action. A human needs to act on this directly.",
      steps: [
        "Read the overdue condition Beacon detected and confirm it's still true.",
        "Take the action outside Beacon (email, call, escalate, push paper).",
        "Accept this task to acknowledge — it will re-emit on the next refresh if the condition persists.",
      ],
    },
  };

  return byAction[task.recommended_action] ?? {
    intro: "If accepted, Beacon will complete the recommended CRM action for this item.",
    steps: [
      "Use the latest synced activity and task context.",
      "Apply the recommended change in Beacon.",
      "Record the action in the timeline so the team can follow what happened.",
    ],
  };
}
