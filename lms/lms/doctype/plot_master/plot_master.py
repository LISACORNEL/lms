import frappe
from frappe.model.document import Document
from frappe.utils import flt

from lms.lms.doctype.land_acquisition.land_acquisition import (
	sync_land_acquisition_plot_summary,
	validate_coordinate_pair,
)

PLOT_TYPE_TO_ITEM = {
	"Residential": "RESIDENTIAL PLOT",
	"Commercial": "COMMERCIAL PLOT",
	"Mixed-Use": "MIXED USED PLOT",
}

PLOT_TYPE_TO_RATE_FIELD = {
	"Residential": "residential_selling_price_per_sqm_tzs",
	"Commercial": "commercial_selling_price_per_sqm_tzs",
	"Mixed-Use": "mixed_use_selling_price_per_sqm_tzs",
}


class PlotMaster(Document):

	def validate(self):
		self.validate_land_acquisition()
		self.fill_acquisition_name()
		self.fill_sales_defaults()
		self.fill_location_coordinates()
		self.fill_financials()
		validate_coordinate_pair(self)
		self.validate_duplicate_plot_number()
		self.validate_selling_price()

	def fill_acquisition_name(self):
		if not self.land_acquisition:
			self.acquisition_name = ""
			return
		self.acquisition_name = frappe.db.get_value(
			"Land Acquisition", self.land_acquisition, "acquisition_name"
		) or ""

	def fill_sales_defaults(self):
		if not self.land_acquisition:
			self.booking_fee_percent = 0
			self.government_share_percent = 0
			self.payment_completion_days = 0
			self.selling_price_per_sqm_tzs = 0
			return

		defaults = frappe.db.get_value(
			"Land Acquisition",
			self.land_acquisition,
			[
				"booking_fee_percent",
				"government_share_percent",
				"payment_completion_days",
				"residential_selling_price_per_sqm_tzs",
				"commercial_selling_price_per_sqm_tzs",
				"mixed_use_selling_price_per_sqm_tzs",
				"default_selling_price_per_sqm_tzs",
			],
			as_dict=True,
		) or {}

		self.booking_fee_percent = flt(defaults.get("booking_fee_percent"))
		self.government_share_percent = flt(defaults.get("government_share_percent"))
		self.payment_completion_days = int(defaults.get("payment_completion_days") or 0)
		self.selling_price_per_sqm_tzs = get_plot_type_selling_rate(defaults, self.plot_type)

	def fill_location_coordinates(self):
		if not self.land_acquisition:
			return

		coordinates = frappe.db.get_value(
			"Land Acquisition",
			self.land_acquisition,
			["latitude", "longitude"],
			as_dict=True,
		) or {}

		if self.latitude in (None, "") and coordinates.get("latitude") not in (None, ""):
			self.latitude = flt(coordinates.get("latitude"))
		if self.longitude in (None, "") and coordinates.get("longitude") not in (None, ""):
			self.longitude = flt(coordinates.get("longitude"))

	def validate_land_acquisition(self):
		if not self.land_acquisition:
			return
		status = frappe.db.get_value("Land Acquisition", self.land_acquisition, "status")
		if status not in ("Approved", "Subdivided"):
			frappe.throw(
				f"Land Acquisition {self.land_acquisition} is not ready for subdivision "
				f"(current status: {status}). Only Approved/Subdivided acquisitions can be used."
			)

	def fill_financials(self):
		if not self.land_acquisition:
			self.cost_per_sqm = 0
			self.allocated_cost = 0
			self.selling_price = 0
			return

		la_doc = frappe.get_doc("Land Acquisition", self.land_acquisition)
		total_sqm = flt(la_doc.total_area_sqm)
		plot_sqm = flt(self.plot_size_sqm)

		self.cost_per_sqm = 0
		self.allocated_cost = 0

		if flt(la_doc.acquisition_cost_tzs) > 0 and total_sqm > 0:
			self.cost_per_sqm = flt(la_doc.get("cost_per_sqm_tzs")) or (flt(la_doc.acquisition_cost_tzs) / total_sqm)
			if plot_sqm > 0:
				self.allocated_cost = self.cost_per_sqm * plot_sqm

		if flt(self.selling_price_per_sqm_tzs) > 0 and plot_sqm > 0:
			self.selling_price = flt(self.selling_price_per_sqm_tzs) * plot_sqm
		else:
			self.selling_price = 0

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
		if flt(self.selling_price_per_sqm_tzs) <= 0:
			frappe.throw(
				f"Set the {get_plot_type_rate_label(self.plot_type)} on Land Acquisition "
				f"{self.land_acquisition} before creating this plot."
			)
		if flt(self.selling_price) <= 0:
			frappe.throw("Selling Price must be greater than zero.")

	def on_submit(self):
		self.create_stock_entry()
		sync_land_acquisition_plot_summary(self.land_acquisition)

	def on_cancel(self):
		self.cancel_stock_entry()
		sync_land_acquisition_plot_summary(self.land_acquisition)

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

		serial_number = self.name  # PLT-2024-0001 — globally unique, ties stock to this plot

		# Pre-create the Serial No record so ERPNext's validate_serialized_batch()
		# (which runs on insert, before on_submit) finds it and doesn't throw.
		if not frappe.db.exists("Serial No", serial_number):
			sn = frappe.get_doc({
				"doctype": "Serial No",
				"serial_no": serial_number,
				"item_code": item_code,
				"company": settings.company,
			})
			sn.insert(ignore_permissions=True)

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
					"serial_no": serial_number,
					"use_serial_batch_fields": 1,
				}
			],
		})

		se.insert(ignore_permissions=True)
		se.submit()

		self.db_set("stock_entry", se.name)
		self.db_set("serial_no", serial_number)

		frappe.msgprint(
			f"Plot entered inventory. Stock Entry: {se.name} | Serial No: {serial_number}",
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


def get_plot_type_selling_rate(acquisition_values, plot_type):
	rate_field = PLOT_TYPE_TO_RATE_FIELD.get(plot_type)
	if rate_field:
		rate = flt((acquisition_values or {}).get(rate_field))
		if rate > 0:
			return rate

	return flt((acquisition_values or {}).get("default_selling_price_per_sqm_tzs"))


def get_plot_type_rate_label(plot_type):
	return {
		"Residential": "Residential Rate per sqm (TZS)",
		"Commercial": "Commercial Rate per sqm (TZS)",
		"Mixed-Use": "Mixed-Use Rate per sqm (TZS)",
	}.get(plot_type, "plot selling rate")
