"""Pure-logic tests for company lifecycle helpers (no DB)."""
import unittest

from app.models.company import Company
from app.services.account_sourcing import _clean_domain
from app.services.company_lifecycle import company_domain_family, normalized_alias_domains


class CleanDomainTests(unittest.TestCase):
    def test_url_paste_normalizes(self) -> None:
        self.assertEqual(_clean_domain("https://www.Acme.com/about?x=1#y"), "acme.com")

    def test_email_paste_yields_its_domain(self) -> None:
        self.assertEqual(_clean_domain("info@acme.com"), "acme.com")

    def test_port_stripped(self) -> None:
        self.assertEqual(_clean_domain("acme.com:8080"), "acme.com")

    def test_aggregators_and_freemail_rejected(self) -> None:
        # These can never identify a COMPANY — a linkedin.com "domain" made the
        # orphan mapper sweep unrelated contacts under one account in prod.
        self.assertEqual(_clean_domain("linkedin.com/company/acme"), "")
        self.assertEqual(_clean_domain("gmail.com"), "")

    def test_no_dot_is_not_a_domain(self) -> None:
        self.assertEqual(_clean_domain("localhost"), "")


class DomainFamilyTests(unittest.TestCase):
    def test_family_is_primary_plus_normalized_aliases(self) -> None:
        company = Company(
            name="Dayforce", domain="dayforce.com",
            additional_domains=["Ceridian.com ", "ceridian.com", ""],
        )
        self.assertEqual(normalized_alias_domains(company), ["ceridian.com"])
        self.assertEqual(company_domain_family(company), {"dayforce.com", "ceridian.com"})

    def test_no_aliases_is_just_primary(self) -> None:
        company = Company(name="Acme", domain="acme.com")
        self.assertEqual(company_domain_family(company), {"acme.com"})


if __name__ == "__main__":
    unittest.main()
