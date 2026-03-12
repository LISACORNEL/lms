import frappe
from frappe.model.document import Document


ACCOUNT_FIELD_RULES = {
    "land_under_development_account": {"root_type": "Asset"},
    "plot_inventory_account": {"root_type": "Asset"},
    "customer_advance_account": {"root_type": "Liability"},
    "revenue_account": {"root_type": "Income"},
    "cogs_account": {"root_type": "Expense"},
    "tcb_bank_account": {"root_type": "Asset", "account_type": "Bank"},
    "government_payable_account": {"root_type": "Liability"},
    "forfeited_deposits_account": {"root_type": "Income"},
    "seller_payable_account": {"root_type": "Liability"},
    "application_fee_income_account": {"root_type": "Income"},
    "application_fee_receiving_account": {"root_type": "Asset", "account_type": ("Bank", "Cash")},
}


class LMSSettings(Document):

    def validate(self):
        self.validate_accounts()

    def validate_accounts(self):
        if not self.company:
            return

        # List of all account fields to validate
        account_fields = [
            "land_under_development_account",
            "plot_inventory_account",
            "customer_advance_account",
            "revenue_account",
            "cogs_account",
            "tcb_bank_account",
            "government_payable_account",
            "forfeited_deposits_account",
            "seller_payable_account",
            "application_fee_income_account",
            "application_fee_receiving_account",
        ]

        # Required fields for core operation
        required_fields = [
            "land_under_development_account",
            "plot_inventory_account",
            "customer_advance_account",
            "revenue_account",
            "cogs_account",
            "government_payable_account",
            "application_fee_income_account",
        ]

        # Expected account types for each field
        account_types = {
            "land_under_development_account": "Asset",
            "plot_inventory_account": "Asset",
            "customer_advance_account": "Liability",
            "revenue_account": "Income",
            "cogs_account": "Expense",
            "government_payable_account": "Liability",
            "application_fee_income_account": "Income",
            "forfeited_deposits_account": "Income",
            "seller_payable_account": "Liability",
            "tcb_bank_account": "Asset",
            "application_fee_receiving_account": "Asset",
        }

        account_type_constraints = {
            "tcb_bank_account": {"Bank"},
            "application_fee_receiving_account": {"Bank", "Cash"},
        }

        for field in account_fields:
            account = self.get(field)
            if not account:
                if field in required_fields:
                    raise frappe.ValidationError(
                        f"Account field '{self.meta.get_field(field).label}' is required.")
                continue
            company = frappe.db.get_value("Account", account, "company")
            if company and company != self.company:
                frappe.throw(
                    f"Account '{account}' in field "
                    f"'{self.meta.get_field(field).label}' "
                    f"belongs to company '{company}', not '{self.company}'."
                )
            expected_type = account_types.get(field)
            if expected_type:
                root_type = frappe.db.get_value("Account", account, "root_type")
                if root_type and root_type != expected_type:
                    frappe.throw(
                        f"Account '{account}' in field "
                        f"'{self.meta.get_field(field).label}' "
                        f"is type '{root_type}', expected '{expected_type}'."
                    )

            allowed_account_types = account_type_constraints.get(field)
            if allowed_account_types:
                account_type = frappe.db.get_value("Account", account, "account_type")
                if account_type and account_type not in allowed_account_types:
                    allowed = ", ".join(sorted(allowed_account_types))
                    frappe.throw(
                        f"Account '{account}' in field "
                        f"'{self.meta.get_field(field).label}' "
                        f"is account type '{account_type}', expected one of: {allowed}."
                    )
