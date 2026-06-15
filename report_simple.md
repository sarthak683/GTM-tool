# 🔍 Beacon CRM — Deep Test Report (Sales Rep POV)

---

## EXECUTIVE SUMMARY

Beacon CRM is a genuinely impressive, AI-forward GTM execution platform — not just a record-keeping CRM. The core concept is strong: a rep-first tool that combines pipeline management, AI-driven outreach, pre-meeting intelligence, task automation, and analytics. However, there are several bugs, UX friction points, and missing features that reduce its effectiveness for daily sales use.

---

## 🐛 BUGS & ERRORS

### Critical Bugs

**1. Sales Analytics — Large Blank Content Area (High Severity)**
On the Sales Analytics page, there is a ~200px blank white gap between the introductory text ("A manager-friendly read on rep activity...") and the Snapshot/filter panel. This appears to be a missing content block or failed component render. As a sales rep, I see an empty zone where something should clearly exist — likely a chart or KPI row. The tab navigation is pushed far below the viewport on first load, making users think the page is broken.

**2. Team Management — Count Mismatch (Medium Severity)**
The "Seed Beacon Team" button displays **(13 members)** but the actual loaded member count is **11**, with metrics showing: 4 Admins, 6 AEs, 1 SDR. These numbers add up to 11, not 13. Either the button pulls from a stale cache or the count includes deactivated/pending users without labeling them differently.

**3. Tasks — Displays "0" Before Data Loads (Medium Severity)**
When you open the Tasks page, the summary cards flash "0 open, 0 recommendations, 0 manual, 0 overdue, 0 due today" for ~2 seconds before showing the real counts (4, 4, 0, 1, 1 respectively). This creates confusion and makes the page feel broken on first load. A skeleton loader or spinner should replace the zeroed stats during the load phase.

**4. Manual Task With Title "0.0" (Medium Severity)**
In the AgencyBloc deal → Tasks tab, a manual task exists with the title **"0.0"** (a raw float/numeric value). This is a data entry/validation bug — the task title field accepted a number and stored it without validation. Task titles should require at least a meaningful string input.

**5. Account Domain Showing "opengovinc.unknown" (Low-Medium Severity)**
In Prospecting, the contact Angelica Au (OpenGov Inc.) shows domain as **"opengovinc.unknown"** — a failed domain resolution fallback that surfaced in the UI. The `.unknown` TLD should either be hidden, replaced with "Domain not found," or trigger a re-enrichment prompt rather than displaying a junk string.

**6. Search Result Showing Numeric ID as Domain (Low Severity)**
In Quick Search, one Chargebee account shows **"118341895908 • Account"** — a numeric ID where a company name or domain should be. This is likely an imported record with no proper name resolution. It should fall back to displaying the company name or "No domain."

**7. Deal Amount Not Formatted in Edit Mode (Low Severity)**
Inside the deal drawer for QAD, the Amount field displays as **"75000.00"** (raw float) instead of a currency-formatted **"$75,000.00"**. On the kanban card it correctly shows $75,000, but the form input is inconsistent.

---

## 📐 UI/UX ISSUES

### Navigation & Information Architecture

**1. Page Titles and Headers Are Redundant**
The browser tab title says "Beacon CRM" for Tasks (instead of "Tasks — Beacon CRM"), and some pages like Tasks show "Beacon CRM / Enterprise GTM execution workspace" as the header — not the page title "Tasks." Every page should have a context-specific h1 matching the section. A user with multiple CRM tabs open can't tell them apart.

**2. Loading States Are Inconsistent**
Some sections show a spinner ("Loading meetings...", "Loading contact profile...", "Loading tasks...") while others flash zero-state cards. The app needs a consistent skeleton UI pattern across all list/data pages.

**3. Pipeline Board — "Drag to move stages · Click to open deal" Only Visible on Hover**
The instruction hint in the top-right of the kanban board ("Drag to move stages · Click to open deal") is easy to miss for new users. There's no onboarding affordance or tooltip on first use to explain the drag-and-drop interactions.

**4. Deal Drawer — No "Open Full Page" Option**
The deal detail only opens as a side drawer/modal. For complex deals with a lot of MEDDPICC data, activity logs, and email threads, a rep needs a full-page view to work efficiently. There's no "Open in full page" button.

**5. MEDDPICC Tab — No Notes Field Per Dimension**
Each MEDDPICC item (Metrics, Economic Buyer, Decision Criteria, etc.) has a status toggle (Not Started / Identified / Validated / Confirmed) but **no free-text notes box**. Reps can't document *why* something is "Identified" or record supporting evidence. This significantly limits the framework's usefulness.

**6. Pipeline Sidebar — Filter Panel Always Visible, Wastes Horizontal Space**
The left panel with funnel summary, filters, and assignee takes up ~230px permanently. On smaller screens or laptops, this leaves the kanban board very cramped. A collapsible sidebar or a "compact mode" toggle would help.

**7. Deal Cards — Cryptic Signal Labels ("No signal", "Stale", "Watch")**
The REP and CL (Client) signal badges on deal cards show labels like "No signal," "Stale," "Watch," and "Active" — but there's no tooltip or legend explaining what these mean. A hover tooltip explaining each state would save reps from having to look this up elsewhere.

**8. Date Format Inconsistency**
Some dates use MM-DD-YYYY (e.g., "10-12-2025" in the deal form), others use "Dec 9, 2025, 6:00 PM" on the card, and "Feb 20, 2026, 11:19 AM" in the drawer header. The app should use one consistent date format throughout, preferably "Dec 9, 2025" for readability.

**9. Meetings — "Needs Review" Tag is Orange/Yellow but Not Explained**
Multiple meetings have a "NEEDS REVIEW" badge. What does this mean? What action is required? There's no in-context explanation. A rep seeing this doesn't know if the meeting is pending company linkage, missing attendees, or something else.

**10. Account Sourcing List — No Sorting Controls**
The accounts list shows COLD/low priority items with no way to sort by enrichment status, ICP tier, AE assignment, or date added. Reps have to scroll through all 75 to prioritize.

**11. Pre-Meeting Assistance — Warning Banner Is Easily Missed**
The orange warning "Beacon did not find a confident company and deal link for this meeting yet" on individual meeting prep cards is displayed in the meeting detail, but in the Pre-Meeting Assistance list view, there's no visual indicator that a meeting has this issue — the meeting looks just as "Intel ready" as others.

**12. Tasks Page — No Bulk Actions**
With 4 recommendations showing, you can only action them one by one. There's no "dismiss all," "complete all," or "accept all" bulk action, which slows down daily task triage.

---

## 📊 DATA SUFFICIENCY (Sales Rep Perspective)

### What's Good ✅

The data quality for active deals is actually quite good in several areas. Deals have company linkage, close dates, health scores (Red/Yellow/Green), assigned rep, Next Step notes, tags, email sync CC, and MEDDPICC framework tracking. The prospecting data (811 contacts) is well-structured with name, title, company, email, phone, LinkedIn, persona classification, and outreach readiness scoring. The Beacon Readout AI surfaced 5 useful insights instantly (e.g., "46 open deals have overdue close dates," "48 active deals are missing an expected close date," "REPROSPECT is the slowest stage").

### What's Missing or Weak ⚠️

**For a sales rep, the following data gaps are real blockers:**

**Deal Level:** No "Deal Title" separate from Company name — QAD deal is just called "QAD," making it hard to differentiate multiple deals with the same company. There's no "Deal Type" field (new logo vs. expansion vs. renewal). No "Competitor" field in the deal overview (only visible in account sourcing). No "Product Line" or "Use Case" field. No "Contract Length" or "Pricing Tier" field. No "Last Activity Date" shown on the deal card itself.

**Contact/People Data:** On 5 deals tested, "People on this deal (0)" — zero contacts are linked to deals. As a rep, if I open a deal and there are no contacts, I can't see who to call, email, or what their roles are. The contacts exist in Prospecting (811 of them) but they're not associated to deals. This is a significant CRM hygiene issue that needs AI-assisted linking.

**Pipeline Health:** 448 of 571 deals (78.6%!) are "Inactive." The Reprospect stage alone has $1.16M in pipeline that appears stagnant with "No signal" on both REP and CL sides. The funnel shows 24 ToFU and 21 BoFU but only 16 MoFU — a conversion bottleneck in the middle that isn't surfaced as an alert to reps.

**Source Attribution:** Many deals show "Select source" (unfilled) in the overview, meaning deal source data is missing. This hurts attribution analytics.

**Account Sourcing:** 0 Hot Accounts, 0 Warm Accounts, 0 High Priority — all 75 sourced accounts are COLD with "low" priority. No ICP research has been run (0 researched, 0 target verdicts). Without this data, reps can't prioritize outreach.

---

## 🤖 AI/AUTOMATION FEATURES — ASSESSMENT

### What's Working Well 🟢

The AI features are genuinely differentiated. MEDDPICC Auto-fill with Beacon AI (in the deal drawer) can auto-suggest qualification states from email/activity signals. The Outreach Sequence Generator ("Generate Sequence" button) uses AI to create personalized multi-step email sequences per prospect. The Pre-Meeting Intelligence auto-generates an account brief 12 hours before each call. The Demo Strategy & Story Lineup section (GPT-4o powered) builds tailored demo playbooks with opening hooks, discovery questions, story chapters, objection handling, and next steps. The Beacon Readout is an AI operating summary that converts raw data into 5 quick natural-language insights for managers. Task AI Recommendations ("Sales AI — CRM Updates") surfaces deal-stage, close-date, and MEDDPICC change suggestions from buyer signals. The REP/CL signal indicators track email open, reply, and engagement velocity automatically.

### What's Missing or Needs Improvement 🔴

**AI Deal Scoring:** There's no overall AI deal score or win-probability percentage on the deal card. Reps should be able to see at a glance "this deal is 73% likely to close." The MEDDPICC score (4/24) exists but isn't translated into a win probability.

**AI-Suggested Next Best Action:** On the deal card, the "Next Step" field is free text — a rep types it manually. The system should suggest the next best action automatically based on deal stage, engagement signals, and MEDDPICC gaps. For example: "You haven't engaged the Economic Buyer — suggest scheduling a QBR."

**Automated Follow-up Reminders:** Deals with no REP activity for 7+ days should auto-generate a task. Currently, the system flags "stale" signal on the card but doesn't push a reminder into the rep's queue automatically.

**AI Email Drafting from Deal Context:** In the Emails tab of a deal, reps can only see synced threads. There's no "Draft a follow-up email" button that uses deal context (stage, next step, MEDDPICC state) to pre-write an email for the rep.

**Call Intelligence Integration Gap:** AirCall is mentioned as integrated (the "AirCall Off" button in Prospecting), but call recordings/transcripts aren't surfaced in the deal activity timeline. If a rep makes a call, there's no automatic note or transcript in the deal.

**No AI Deal Deduplication:** There are clearly duplicate-risk entries (e.g., two "Chargebee" accounts in search, multiple meetings titled "Core & Exit KT sessions | Beacon & PS"). An AI deduplication suggestion engine would help.

**Sequence Personalization Depth:** The AI sequence generation exists, but there's no visible preview of the generated emails before activation, and no A/B testing of subject lines or messaging variants.

---

## 💡 TOP IMPROVEMENT RECOMMENDATIONS (Prioritized)

**P1 — Fix Immediately:**
Fix the blank content area in Sales Analytics header. Fix the task "0.0" title validation. Fix the Team member count mismatch. Implement skeleton loaders everywhere instead of zero-flash states.

**P2 — High Impact for Sales Reps:**
Auto-link contacts from Prospecting to their corresponding Deals (AI-assisted). Add MEDDPICC dimension notes fields. Add a "win probability %" to each deal card using MEDDPICC score + engagement signals. Surface "no contacts on deal" as a hygiene warning in Tasks. Fix the "opengovinc.unknown" domain display.

**P3 — UX Polish:**
Standardize date format app-wide. Add tooltip legend for REP/CL signal badges. Add a "Open Full Page" button on the deal drawer. Add sorting controls to Account Sourcing list. Add bulk actions to Tasks. Explain "NEEDS REVIEW" tag contextually. Format deal amounts as currency in edit mode.

**P4 — New AI/Automation Features:**
Build an AI "Next Best Action" recommendation surfaced directly on the deal card. Add AI email draft button in the deal's Emails tab using deal context. Surface AirCall call transcripts/summaries in the deal activity log. Add deal win-probability scoring using combined MEDDPICC + engagement + time-in-stage data. Build AI-powered deduplication for accounts and deals. Add "stale deal" automated task generation (trigger: >7 days no rep activity). Enable A/B testing for AI-generated outreach sequences.

**P5 — Data Completeness:**
Run enrichment on all 75 sourced accounts to generate ICP scores and TAL verdicts. Create a data hygiene workflow that prompts reps to fill missing source, geography, and close date fields. Add Campaign/Initiative tracking fields to deals.

---

## OVERALL VERDICT

**Beacon CRM scores 7.5/10** as a sales rep tool. The product vision is excellent — it's clearly built for execution, not just record-keeping. The AI features (demo strategy, pre-meeting brief, outreach sequence generation, MEDDPICC auto-fill) are genuinely useful differentiators. The biggest blockers right now are the disconnection between Prospecting contacts and Pipeline deals (reps can't see who to call on a deal), too many stale/unprioritized deals with "no signal," several data display bugs, and the blank area in Sales Analytics. Fix those, and this becomes a 9/10 daily-driver CRM for an enterprise AE.