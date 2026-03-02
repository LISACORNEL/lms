import frappe
from frappe.model.document import Document
from frappe.utils import flt


PLOT_TYPE_TO_ITEM = {
	"Residential": "RESIDENTIAL-PLOT",
	"Commercial": "COMMERCIAL-PLOT",
	"Mixed-Use": "MIXED-USE-PLOT",
}


class PlotMaster(Document):

	def validate(self):
		self.validate_land_acquisition()
		self.fill_allocated_cost()
		self.validate_duplicate_plot_number()
		self.validate_selling_price()

	def validate_land_acquisition(self):
		if not self.land_acquisition:
			return
		status = frappe.db.get_value("Land Acquisition", self.land_acquisition, "status")
		if status != "Approved":
			frappe.throw(
				f"Land Acquisition {self.land_acquisition} is not Approved (current status: {status}). "
				"Only plots from Approved Land Acquisitions can be created."
			)

	def fill_allocated_cost(self):
		if self.land_acquisition and not flt(self.allocated_cost):
			cost = frappe.db.get_value(
				"Land Acquisition", self.land_acquisition, "acquisition_cost_tzs"
			)
			expected_plots = frappe.db.get_value(
				"Land Acquisition", self.land_acquisition, "total_area_sqm"
			)
			# allocated_cost_per_plot is stored on Land Acquisition
			allocated = frappe.db.get_value(
				"Land Acquisition", self.land_acquisition, "acquisition_cost_tzs"
			)
			# Get it from the document directly
			la_doc = frappe.get_doc("Land Acquisition", self.land_acquisition)
			if flt(la_doc.acquisition_cost_tzs) > 0 and flt(self.plot_size_sqm) > 0:
				# Cost per sqm * this plot's sqm
				total_sqm = flt(la_doc.total_area_sqm)
				if total_sqm > 0:
					cost_per_sqm = flt(la_doc.acquisition_cost_tzs) / total_sqm
					self.allocated_cost = cost_per_sqm * flt(self.plot_size_sqm)

	def validate_duplicate_plot_number(self):
		if not self.plot_number or not self.land_acquisition:
			return
		existing = frappe.db.get_value(
			"Plot Master",
			{
				"land_acquisition": self.land_acquisition,
				"plot_number": self.plot_number,
				"name": ("!=", self.name),
				"docstatus": ("!=", 2),
			},
			"name",
		)
		if existing:
			frappe.throw(
				f"Plot number '{self.plot_number}' already exists for Land Acquisition "
				f"{self.land_acquisition} (see {existing})."
			)

	def validate_selling_price(self):
		if flt(self.selling_price) <= 0:
			frappe.throw("Selling Price must be greater than zero.")

	def on_submit(self):
		self.create_stock_entry()

	def on_cancel(self):
		self.cancel_stock_entry()

	def create_stock_entry(self):
		settings = frappe.get_single("LMS Settings")

		item_code = PLOT_TYPE_TO_ITEM.get(self.plot_type)
		if not item_code:
			frappe.throw(f"No item mapped for plot type '{self.plot_type}'.")

		warehouse = settings.plot_inventory_warehouse
		if not warehouse:
			frappe.throw("Plot Inventory Warehouse not set in LMS Settings.")

		land_account = settings.land_under_development_account
		if not land_account:
			frappe.throw("Land Under Development account not set in LMS Settings.")

		se = frappe.get_doc({
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"posting_date": frappe.utils.today(),
			"company": settings.company,
			"remarks": f"Plot {self.plot_number} from {self.land_acquisition}",
			"difference_account": land_account,
			"items": [
				{
					"item_code": item_code,
					"qty": 1,
					"basic_rate": flt(self.allocated_cost),
					"t_warehouse": warehouse,
				}
			],
		})

		se.insert(ignore_permissions=True)
		se.submit()

		self.db_set("stock_entry", se.name)

		frappe.msgprint(
			f"Plot entered inventory. Stock Entry: {se.name}",
			indicator="green",
			alert=True,
		)

	def cancel_stock_entry(self):
		if not self.stock_entry:
			return
		se_doc = frappe.get_doc("Stock Entry", self.stock_entry)
		if se_doc.docstatus == 1:
			se_doc.cancel()
		self.db_set("stock_entry", None)
		self.db_set("serial_no", None)
