"""
Regression tests for Workday company-name extraction.

The company should come from the Workday *site* path (e.g. "Samsung_Careers"),
not the tenant subdomain (e.g. "sec" for Samsung Electronics). Pure function —
no network. Run: python -m unittest test_workday_company
"""
import unittest

from ats_scraper import _workday_company


class TestWorkdayCompany(unittest.TestCase):
    def test_site_names_the_company_not_the_tenant(self):
        # The reported bug: tenant "sec" must not become the company.
        self.assertEqual(_workday_company("sec", "Samsung_Careers"), "Samsung")

    def test_common_company_careers_sites(self):
        for tenant, site, expected in [
            ("remitly", "Remitly_Careers", "Remitly"),
            ("nordstrom", "nordstrom_careers", "Nordstrom"),
            ("cisco", "Cisco_Careers", "Cisco"),
            ("netflix", "Netflix", "Netflix"),
        ]:
            self.assertEqual(_workday_company(tenant, site), expected, site)

    def test_camelcase_sites_are_split(self):
        self.assertEqual(_workday_company("blueorigin", "BlueOrigin"), "Blue Origin")

    def test_short_acronyms_preserved(self):
        self.assertEqual(_workday_company("generalmotors", "Careers_GM"), "GM")

    def test_generic_site_falls_back_to_tenant(self):
        # Nothing meaningful in the site → use the tenant.
        self.assertEqual(_workday_company("greystar", "External"), "Greystar")
        self.assertEqual(
            _workday_company("salesforce", "External_Career_Site"), "Salesforce")

    def test_empty_site_falls_back_to_tenant(self):
        self.assertEqual(_workday_company("acme-corp", ""), "Acme Corp")


if __name__ == "__main__":
    unittest.main()
