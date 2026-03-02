import frappe
from frappe.model.document import Document
from frappe.utils import flt, add_days


class PlotContract(Document):

	def validate(self):
		self.validate_plot_available()
		self.fill_selling_price()
		self.calculate_financials()
		self.calculate_payment_summary()

	def validate_plot_available(self):
		if not self.plot:
			return
		# Only block if this is a brand-new unsaved document
		if not frappe.db.exists("Plot Contract", self.name):
			plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
			if plot_status != "Available":
				frappe.throw(
					f"Plot {self.plot} is not Available (current status: {plot_status}). "
					"Only Available plots can be contracted."
				)

	def fill_selling_price(self):
		if self.plot and not flt(self.selling_price):
			self.selling_price = frappe.db.get_value("Plot Master", self.plot, "selling_price")

	def calculate_financials(self):
		settings = frappe.get_single("LMS Settings")
		self.booking_fee_percent = flt(settings.booking_fee_percent)
		if flt(self.selling_price) > 0:
			self.booking_fee_amount = flt(self.selling_price) * self.booking_fee_percent / 100
			self.balance_due = flt(self.selling_price) - self.booking_fee_amount
		if self.contract_date and not self.payment_deadline:
			days = int(settings.payment_completion_days or 90)
			self.payment_deadline = add_days(self.contract_date, days)

	def calculate_payment_summary(self):
		self.total_contract_value = flt(self.selling_price)
		total_paid = sum(flt(row.paid_amount) for row in self.payment_schedule)
		self.total_paid = total_paid
		self.total_outstanding = flt(self.selling_price) - total_paid
		gov_schedule = frappe.get_single("Government Fee Schedule")
		gov_pct = flt(gov_schedule.government_share_percent)
		if gov_pct > 0:
			self.government_fee_withheld = total_paid * gov_pct / 100

	def on_submit(self):
		frappe.db.set_value("Plot Master", self.plot, "status", "Reserved")
		self.db_set("contract_status", "Active")

	def on_cancel(self):
		frappe.db.set_value("Plot Master", self.plot, "status", "Available")
		self.db_set("contract_status", "Cancelled")
