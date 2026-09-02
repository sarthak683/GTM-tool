from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer


# PostgreSQL keeps money exact as Decimal. API responses intentionally encode it
# as a JSON number because the frontend's formatting and arithmetic APIs consume
# numbers, and the OpenAPI schema must describe that real wire format.
MoneyDecimal = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json"),
]
