import frappe
from frappe.model.document import Document
from frappe.utils import today, flt


class LandAcquisition(Document):

    def validate(self):
        self.calculate_cost_tzs()
        self.validate_cost()
        self.validate_area()

    def calculate_cost_tzs(self):
        self.acquisition_cost_tzs = flt(self.total_acquisition_cost) * flt(self.exchange_rate or 1)

    def validate_cost(self):
        if flt(self.total_acquisition_cost) <= 0:
            frappe.throw("Total Acquisition Cost must be greater than zero.")

    def validate_area(self):
        if flt(self.total_area_sqm) <= 0:
            frappe.throw("Total Area must be greater than zero.")

    def on_submit(self):
        self.db_set("status", "Pending Approval")

    @frappe.whitelist()
    def approve(self):
        if self.status != "Pending Approval":
            frappe.throw("Only documents in Pending Approval status can be approved.")

        if self.docstatus != 1:
            frappe.throw("Document must be submitted before it can be approved.")

        self.create_journal_entry()

        self.db_set("status", "Approved")
        self.db_set("approved_by", frappe.session.user)
        self.db_set("approval_date", today())
        frappe.msgprint("Land Acquisition approved and journal entry posted.", alert=True)

    def create_journal_entry(self):
        settings = frappe.get_single("LMS Settings")
        company = settings.company
        land_account = settings.land_under_development_account

        if not land_account:
            frappe.throw("Land Under Development account not set in LMS Settings.")

        bank_account = frappe.db.get_value(
            "Account",
            {"account_number": "1201", "company": company},
            "name"
        )

        if not bank_account:
            frappe.throw("Main Operating Bank Account (1201) not found. Please create it in Chart of Accounts.")

        cost_center = frappe.db.get_value(
            "Cost Center",
            {"company": company, "is_group": 0},
            "name"
        )

        amount = flt(self.acquisition_cost_tzs)

        je = frappe.get_doc({
            "doctype": "Journal Entry",
            "voucher_type": "Journal Entry",
            "posting_date": self.acquisition_date,
            "company": company,
            "user_remark": f"Land Acquisition — {self.acquisition_name} ({self.name})",
            "accounts": [
                {
                    "account": land_account,
                    "debit_in_account_currency": amount,
                    "credit_in_account_currency": 0,
                    "cost_center": cost_center
                },
                {
                    "account": bank_account,
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": amount,
                    "cost_center": cost_center
                }
            ]
        })

        je.insert(ignore_permissions=True)
        je.submit()

        self.db_set("journal_entry", je.name)
        frappe.msgprint(f"Journal Entry {je.name} posted successfully.", alert=True)