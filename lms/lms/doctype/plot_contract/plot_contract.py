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
			# Belt-and-suspenders: block if any submitted active contract exists for this plot
			active = frappe.db.exists("Plot Contract", {
				"plot": self.plot,
				"docstatus": 1,
				"contract_status": ["in", ["Active", "Completed"]]
			})
			if active:
				frappe.throw(
					f"Plot {self.plot} already has an active contract ({active}). "
					"The existing contract must be terminated or completed before a new one can be created."
				)

	def fill_selling_price(self):
		if self.plot:
			plot_data = frappe.db.get_value(
				"Plot Master", self.plot,
				["selling_price", "land_acquisition"],
				as_dict=True
			)
			if plot_data:
				if not flt(self.selling_price):
					self.selling_price = plot_data.selling_price
				self.land_acquisition = plot_data.land_acquisition

	def calculate_financials(self):
		if flt(self.selling_price) > 0 and flt(self.booking_fee_percent) > 0:
			self.booking_fee_amount = flt(self.selling_price) * flt(self.booking_fee_percent) / 100
			self.balance_due = flt(self.selling_price) - self.booking_fee_amount
		if self.contract_date and flt(self.payment_completion_days) > 0:
			self.payment_deadline = add_days(self.contract_date, int(self.payment_completion_days))

	def calculate_payment_summary(self):
		self.total_contract_value = flt(self.selling_price)
		total_paid = sum(flt(row.paid_amount) for row in self.payment_schedule)
		self.total_paid = total_paid
		self.total_outstanding = flt(self.selling_price) - total_paid
		if flt(self.government_share_percent) > 0:
			self.government_fee_withheld = flt(self.selling_price) * flt(self.government_share_percent) / 100

	def on_submit(self):
		frappe.db.set_value("Plot Master", self.plot, "status", "Reserved")
		self.db_set("contract_status", "Active")

	def on_cancel(self):
		frappe.db.set_value("Plot Master", self.plot, "status", "Available")
		self.db_set("contract_status", "Cancelled")

	@frappe.whitelist()
	def terminate_contract(self, reason):
		if self.contract_status != "Active":
			frappe.throw("Only Active contracts can be terminated.")
		if self.docstatus != 1:
			frappe.throw("Document must be submitted before it can be terminated.")
		if not reason or not str(reason).strip():
			frappe.throw("A termination reason is required.")

		frappe.db.set_value("Plot Master", self.plot, "status", "Available")
		self.db_set("contract_status", "Terminated")
		self.db_set("termination_reason", str(reason).strip())
		frappe.msgprint(
			f"Contract terminated. Plot {self.plot} is now Available for new contracts.",
			indicator="orange",
			alert=True
		)
