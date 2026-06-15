"""
Contact intelligence service.

Generates a pre-meeting stakeholder profile for a contact:
  1. Pull contact + company data from DB
  2. Playwright scrape of their LinkedIn (if URL known) or Google for their profile
  3. GPT-4o summarises into a persona-aware stakeholder brief

Returns:
  {
    contact_id, contact_name, title, persona,
    company_name, linkedin_scraped: {...},
    brief: "• bullet 1\n• bullet 2\n• bullet 3"
  }
"""
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.claude import ClaudeClient
from app.models.contact import Contact
from app.models.company import Company

logger = logging.getLogger(__name__)
ai_client = ClaudeClient()


async def generate_contact_brief(contact_id: UUID, session: AsyncSession) -> dict:
    contact = await session.get(Contact, contact_id)
    if not contact:
        return {"error": "Contact not found"}

    company: Optional[Company] = None
    if contact.company_id:
        company = await session.get(Company, contact.company_id)

    result = {
        "contact_id": str(contact_id),
        "contact_name": f"{contact.first_name} {contact.last_name}",
        "title": contact.title,
        "persona": contact.persona,
        "email": contact.email,
        "linkedin_url": contact.linkedin_url,
        "company_name": company.name if company else None,
        "scraped": {},
        "brief": None,
    }

    # Attempt Playwright scrape of LinkedIn profile
    scraped: dict = {}
    if contact.linkedin_url:
        try:
            from app.clients.playwright_scraper import scrape_linkedin_profile
            scraped = await scrape_linkedin_profile(contact.linkedin_url)
            result["scraped"] = scraped
        except Exception as e:
            logger.warning(f"LinkedIn scrape failed for {contact.linkedin_url}: {e}")
            scraped = {}

    # GPT-4o stakeholder brief
    result["brief"] = await _generate_stakeholder_brief(
        contact_name=f"{contact.first_name} {contact.last_name}",
        title=contact.title or "Unknown title",
        persona=contact.persona or "unknown",
        company_name=company.name if company else "Unknown company",
        company_industry=company.industry if company else "",
        linkedin_scraped=scraped,
    )
    return result


async def _generate_stakeholder_brief(
    contact_name: str,
    title: str,
    persona: str,
    company_name: str,
    company_industry: str,
    linkedin_scraped: dict,
) -> Optional[str]:
    if ai_client.mock:
        persona_tip = {
            "economic_buyer": "Focus on ROI, total cost of ownership, and risk reduction.",
            "champion": "Give them ammunition — concrete proof points and demo flows they can share internally.",
            "technical_evaluator": "Lead with architecture, security model, and integration depth.",
        }.get(persona, "Lead with value and keep it concise.")

        return (
            f"• {contact_name} holds a {title} role — likely focused on strategic outcomes at {company_name}.\n"
            f"• No LinkedIn data available — research manually before the call.\n"
            f"• Recommended approach: {persona_tip}"
        )

    li_text = ""
    if linkedin_scraped.get("headline"):
        li_text += f"LinkedIn headline: {linkedin_scraped['headline']}\n"
    if linkedin_scraped.get("summary"):
        li_text += f"Summary: {linkedin_scraped['summary'][:500]}\n"
    if linkedin_scraped.get("experience"):
        li_text += f"Recent experience: {str(linkedin_scraped['experience'])[:400]}\n"

    persona_context = {
        "economic_buyer": "This person controls budget. Focus on ROI, business risk, and strategic value.",
        "champion": "This person will sell internally for us. Give them proof points and objection handlers.",
        "technical_evaluator": "This person evaluates the tech. Focus on architecture, security, integration.",
        "unknown": "Persona unknown. Lead with value discovery questions.",
    }.get(persona, "Lead with open discovery.")

    system = (
        "You are an expert sales coach preparing a rep for a stakeholder call. "
        "Write exactly 3 bullet points (start each with •). Be specific and actionable. "
        "Each bullet max 2 sentences."
    )
    user = (
        f"Contact: {contact_name} | Title: {title} | Company: {company_name} ({company_industry})\n"
        f"Persona type: {persona} — {persona_context}\n"
        f"LinkedIn intel:\n{li_text or 'No LinkedIn data available.'}\n\n"
        "Write 3 bullets covering:\n"
        "1. Who this person is and what they care about (infer from title + company)\n"
        "2. Likely priorities or pain points given their role\n"
        "3. Specific conversation angle for Beacon.li (AI implementation orchestration)"
    )

    return await ai_client.complete(system, user, max_tokens=300)
