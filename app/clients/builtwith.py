"""
BuiltWith client — technology stack detection.
"""
from typing import Optional

from app.config import settings


class BuiltWithClient:
    def __init__(self) -> None:
        self.api_key = settings.BUILTWITH_API_KEY
        self.mock = not self.api_key

    async def get_tech_stack(self, domain: str) -> Optional[dict]:
        """Return the detected technology stack for a domain."""
        if self.mock:
            return None
        # Real call: GET https://api.builtwith.com/v21/api.json
        # Params: {"KEY": self.api_key, "LOOKUP": domain}
        return None
