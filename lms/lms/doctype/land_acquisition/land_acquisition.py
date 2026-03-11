import frappe
from frappe.model.document import Document
from frappe.utils import today, flt


@frappe.whitelist()
def sync_land_acquisition_plot_summary(land_acquisition):
    """Recompute and persist submitted Plot Master counts for one Land Acquisition.

    Uses docstatus=1 plots only (active inventory records).
    Also toggles status Approved <-> Subdivided based on whether any plots exist.
    """
    if not land_acquisition or not frappe.db.exists("Land Acquisition", land_acquisition):
        return {}

    rows = frappe.db.get_all(
        "Plot Master",
        filters={
            "land_acquisition": land_acquisition,
            "docstatus": 1,
        },
        fields=["status", "count(name) as cnt"],
        group_by="status",
    )
    status_map = {row.status: int(row.cnt or 0) for row in rows}

    total_plots = int(sum(status_map.values()))
    summary = {
        "total_plots": total_plots,
        "available_plots": int(status_map.get("Available", 0)),
        "reserved_plots": int(status_map.get("Reserved", 0)),
        "delivered_plots": int(status_map.get("Delivered", 0) + status_map.get("Title Closed", 0)),
    }

    la_state = frappe.db.get_value(
        "Land Acquisition",
        land_acquisition,
        ["status", "docstatus"],
        as_dict=True,
    )
    if la_state and la_state.docstatus == 1:
        if total_plots > 0 and la_state.status == "Approved":
            summary["status"] = "Subdivided"
        elif total_plots == 0 and la_state.status == "Subdivided":
            summary["status"] = "Approved"

    frappe.db.set_value("Land Acquisition", land_acquisition, summary, update_modified=False)
    return summary


class LandAcquisition(Document):

    def validate(self):
        self.calculate_total_from_items()
        self.calculate_cost_tzs()
        self.validate_cost()
        self.validate_area()

    def calculate_total_from_items(self):
        self.total_acquisition_cost = sum(flt(row.amount) for row in self.cost_items)

    def calculate_cost_tzs(self):
        self.acquisition_cost_tzs = flt(self.total_acquisition_cost) * flt(self.exchange_rate or 1)

    def validate_cost(self):
        if not self.cost_items:
            frappe.throw("Add at least one cost item in the Cost Breakdown table.")
        if flt(self.total_acquisition_cost) <= 0:
            frappe.throw("Total Acquisition Cost must be greater than zero.")

    def validate_area(self):
        if flt(self.total_area_sqm) <= 0:
            frappe.throw("Total Area must be greater than zero.")

    def on_submit(self):
        self.db_set("status", "Pending Approval")
        sync_land_acquisition_plot_summary(self.name)

    def before_cancel(self):
        """Block cancellation while submitted Plot Masters still exist."""
        active_plot_count = frappe.db.count("Plot Master", {
            "land_acquisition": self.name,
            "docstatus": 1,
        })
        if not active_plot_count:
            return

        sample_plots = frappe.db.get_all(
            "Plot Master",
            filters={"land_acquisition": self.name, "docstatus": 1},
            fields=["name"],
            limit_page_length=3,
        )
        sample_names = ", ".join(row.name for row in sample_plots)
        extra = ""
        if active_plot_count > len(sample_plots):
            extra = f", and {active_plot_count - len(sample_plots)} more"

        frappe.throw(
            "Cannot cancel this Land Acquisition because submitted plots still exist: "
            f"{sample_names}{extra}. Cancel those Plot Master records first."
        )

    def on_cancel(self):
        self.cancel_journal_entry()
        sync_land_acquisition_plot_summary(self.name)

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
        sync_land_acquisition_plot_summary(self.name)
        frappe.msgprint("Land Acquisition approved and journal entry posted.", alert=True)

    def cancel_journal_entry(self):
        """Ensure accounting stays consistent when LA is cancelled."""
        if not self.journal_entry:
            return

        je_doc = frappe.get_doc("Journal Entry", self.journal_entry)
        if je_doc.docstatus == 1:
            je_doc.cancel()
        elif je_doc.docstatus == 0:
            frappe.delete_doc("Journal Entry", je_doc.name, ignore_permissions=True)

    def create_journal_entry(self):
        settings = frappe.get_single("LMS Settings")
        company = settings.company
        land_account = settings.land_under_development_account

        if not land_account:
            frappe.throw("Land Under Development account not set in LMS Settings.")

        seller_payable_account = settings.seller_payable_account
        if not seller_payable_account:
            frappe.throw("Seller Payable account not set in LMS Settings.")

        if not self.seller:
            frappe.throw("Seller (Supplier) must be set before approval so the journal entry can be posted.")

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
                    "account": seller_payable_account,
                    "debit_in_account_currency": 0,
                    "credit_in_account_currency": amount,
                    "cost_center": cost_center,
                    "party_type": "Supplier",
                    "party": self.seller
                }
            ]
        })

        je.insert(ignore_permissions=True)
        je.submit()

        self.db_set("journal_entry", je.name)
        frappe.msgprint(f"Journal Entry {je.name} posted successfully.", alert=True)
