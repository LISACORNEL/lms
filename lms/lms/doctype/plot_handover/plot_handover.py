import frappe
from frappe.model.document import Document
from frappe.utils import today

from lms.lms.doctype.land_acquisition.land_acquisition import sync_land_acquisition_plot_summary
from lms.lms.doctype.plot_master.plot_master import PLOT_TYPE_TO_ITEM


class PlotHandover(Document):

	def validate(self):
		self._ensure_no_existing_handover()
		self._fill_from_contract()

	def on_submit(self):
		delivery_note = self._ensure_delivery_note()
		if delivery_note and self.delivery_note != delivery_note:
			self.db_set("delivery_note", delivery_note)
		frappe.db.set_value("Plot Master", self.plot, "status", "Delivered")
		self._sync_land_acquisition_summary()
		self.db_set("handover_status", "Completed")

	def on_cancel(self):
		self._cancel_delivery_note()
		frappe.db.set_value("Plot Master", self.plot, "status", "Ready for Handover")
		self._sync_land_acquisition_summary()
		self.db_set("handover_status", "Draft")

	def _ensure_no_existing_handover(self):
		if not self.contract:
			return

		existing = frappe.db.get_value(
			"Plot Handover",
			{
				"contract": self.contract,
				"docstatus": 1,
				"name": ("!=", self.name),
			},
			"name",
		)
		if existing:
			frappe.throw(f"Contract {self.contract} already has a submitted Plot Handover: {existing}.")

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

		plot_status = frappe.db.get_value("Plot Master", contract.plot, "status")
		if plot_status not in ("Ready for Handover", "Delivered"):
			frappe.throw(
				f"Plot {contract.plot} is in status {plot_status}. "
				"A handover can only be created when the plot is Ready for Handover."
			)

		self.customer = contract.customer
		self.plot = contract.plot
		self.acquisition_name = contract.acquisition_name
		self.land_acquisition = contract.land_acquisition
		self.contract_date = contract.contract_date
		self.selling_price = contract.selling_price

	def _ensure_delivery_note(self):
		if self.delivery_note and frappe.db.exists("Delivery Note", self.delivery_note):
			dn = frappe.get_doc("Delivery Note", self.delivery_note)
			if dn.docstatus == 1:
				return dn.name
			if dn.docstatus == 0:
				dn.submit()
				return dn.name

		contract = frappe.get_doc("Plot Contract", self.contract)
		plot = frappe.get_doc("Plot Master", self.plot)
		settings = frappe.get_single("LMS Settings")

		if not plot.serial_no:
			frappe.throw(f"Plot {plot.name} is missing its Serial No.")

		if contract.sales_order and frappe.db.exists("Sales Order", contract.sales_order):
			dn = self._make_delivery_note_from_sales_order(contract.sales_order, plot)
		else:
			dn = self._make_delivery_note_direct(contract, plot, settings)

		dn.run_method("set_missing_values")
		dn.run_method("calculate_taxes_and_totals")
		dn.run_method("set_use_serial_batch_fields")
		dn.posting_date = self.handover_date or today()
		dn.set_posting_time = 1
		dn.remarks = f"Plot handover {self.name} for contract {self.contract} / plot {self.plot}"
		dn.insert(ignore_permissions=True)
		dn.submit()
		return dn.name

	def _make_delivery_note_from_sales_order(self, sales_order_name, plot):
		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note

		dn = make_delivery_note(sales_order_name)
		if not dn.items:
			frappe.throw(f"Sales Order {sales_order_name} has no deliverable rows for handover.")

		for row in dn.items:
			row.qty = 1
			row.serial_no = plot.serial_no
			row.use_serial_batch_fields = 1
			row.warehouse = row.warehouse or frappe.db.get_single_value("LMS Settings", "plot_inventory_warehouse")

		return dn

	def _make_delivery_note_direct(self, contract, plot, settings):
		item_code = PLOT_TYPE_TO_ITEM.get(plot.plot_type)
		if not item_code:
			frappe.throw(f"No item is mapped for plot type '{plot.plot_type}'.")

		return frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"customer": contract.customer,
				"company": settings.company,
				"set_warehouse": settings.plot_inventory_warehouse,
				"items": [
					{
						"item_code": item_code,
						"qty": 1,
						"rate": plot.selling_price,
						"warehouse": settings.plot_inventory_warehouse,
						"serial_no": plot.serial_no,
						"use_serial_batch_fields": 1,
						"description": f"Plot handover for {plot.name}",
					}
				],
			}
		)

	def _cancel_delivery_note(self):
		if not self.delivery_note or not frappe.db.exists("Delivery Note", self.delivery_note):
			return

		dn = frappe.get_doc("Delivery Note", self.delivery_note)
		if dn.docstatus == 1:
			dn.cancel()

	def _sync_land_acquisition_summary(self):
		if self.land_acquisition:
			sync_land_acquisition_plot_summary(self.land_acquisition)
