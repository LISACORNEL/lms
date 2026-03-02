import frappe
from frappe.model.document import Document
from frappe.utils import today

class GovernmentFeeSchedule(Document):

    def validate(self):
        if not self.effective_date:
            self.effective_date = today()

        self.last_updated_by = frappe.session.user

        if self.government_share_percent < 0:
            frappe.throw("Government Share Percentage cannot be negative.")

        if self.government_share_percent >= 100:
            frappe.throw("Government Share Percentage cannot be 100% or more.")
