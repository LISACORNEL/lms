import frappe
from frappe.model.document import Document

class LMSSettings(Document):

    def validate(self):
        self.validate_accounts()

    def validate_accounts(self):
        if not self.company:
            return

        account_fields = [
            "land_under_development_account",
            "plot_inventory_account",
            "customer_advance_account",
            "revenue_account",
            "cogs_account",
            "tcb_bank_account",
            "government_payable_account",
            "forfeited_deposits_account",
            "seller_payable_account"
        ]

        for field in account_fields:
            account = self.get(field)
            if not account:
                continue
            company = frappe.db.get_value("Account", account, "company")
            if company and company != self.company:
                frappe.throw(
                    f"Account '{account}' in field "
                    f"'{self.meta.get_field(field).label}' "
                    f"belongs to company '{company}', not '{self.company}'."
                )
