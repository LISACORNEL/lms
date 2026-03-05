import frappe
from frappe.model.document import Document
from frappe.utils import today


class PlotHandover(Document):

	def validate(self):
		self._fill_from_contract()

	def on_submit(self):
		self.db_set("handover_status", "Completed")

	def on_cancel(self):
		self.db_set("handover_status", "Draft")

	def _fill_from_contract(self):
		"""Auto-fill plot and customer details from the linked Plot Contract."""
		if not self.contract:
			return

		contract = frappe.get_doc("Plot Contract", self.contract)

		if contract.contract_status != "Completed":
			frappe.throw(
				f"Contract {self.contract} is not Completed. "
				"A handover document can only be created for a fully paid contract."
			)

		self.customer = contract.customer
		self.plot = contract.plot
		self.acquisition_name = contract.acquisition_name
		self.land_acquisition = contract.land_acquisition
		self.contract_date = contract.contract_date
		self.selling_price = contract.selling_price
