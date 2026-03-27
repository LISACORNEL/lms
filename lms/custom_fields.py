import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
	"Sales Order": [
		{
			"fieldname": "lms_section",
			"fieldtype": "Section Break",
			"label": "LMS",
			"insert_after": "customer_name",
		},
		{
			"fieldname": "plot",
			"fieldtype": "Link",
			"label": "Plot",
			"options": "Plot Master",
			"insert_after": "lms_section",
		},
		{
			"fieldname": "land_acquisition",
			"fieldtype": "Link",
			"label": "Land Acquisition",
			"options": "Land Acquisition",
			"read_only": 1,
			"insert_after": "plot",
		},
		{
			"fieldname": "acquisition_name",
			"fieldtype": "Data",
			"label": "Acquisition Name",
			"read_only": 1,
			"insert_after": "land_acquisition",
		},
		{
			"fieldname": "plot_application",
			"fieldtype": "Link",
			"label": "Plot Application",
			"options": "Plot Application",
			"insert_after": "acquisition_name",
		},
		{
			"fieldname": "plot_contract",
			"fieldtype": "Link",
			"label": "Plot Contract",
			"options": "Plot Contract",
			"read_only": 1,
			"insert_after": "plot_application",
		},
		{
			"fieldname": "plot_sales_invoice",
			"fieldtype": "Link",
			"label": "Plot Sales Invoice",
			"options": "Sales Invoice",
			"read_only": 1,
			"insert_after": "plot_contract",
		},
		{
			"fieldname": "control_number",
			"fieldtype": "Data",
			"label": "TCB Control Number",
			"read_only": 1,
			"in_list_view": 1,
			"insert_after": "plot_sales_invoice",
		},
		{
			"fieldname": "lms_col_break",
			"fieldtype": "Column Break",
			"insert_after": "control_number",
		},
		{
			"fieldname": "booking_fee_percent",
			"fieldtype": "Percent",
			"label": "Booking Fee %",
			"read_only": 1,
			"insert_after": "lms_col_break",
		},
		{
			"fieldname": "government_share_percent",
			"fieldtype": "Percent",
			"label": "Government Share %",
			"read_only": 1,
			"insert_after": "booking_fee_percent",
		},
		{
			"fieldname": "payment_completion_days",
			"fieldtype": "Int",
			"label": "Payment Completion Days",
			"read_only": 1,
			"insert_after": "government_share_percent",
		},
		{
			"fieldname": "payment_deadline",
			"fieldtype": "Date",
			"label": "Payment Deadline",
			"read_only": 1,
			"insert_after": "payment_completion_days",
		},
		{
			"fieldname": "plot_outstanding_amount",
			"fieldtype": "Currency",
			"label": "Outstanding Amount",
			"read_only": 1,
			"insert_after": "advance_paid",
		},
	],
	"Sales Invoice": [
		{
			"fieldname": "lms_section",
			"fieldtype": "Section Break",
			"label": "LMS",
			"insert_after": "customer_name",
		},
		{
			"fieldname": "plot",
			"fieldtype": "Link",
			"label": "Plot",
			"options": "Plot Master",
			"read_only": 1,
			"insert_after": "lms_section",
		},
		{
			"fieldname": "land_acquisition",
			"fieldtype": "Link",
			"label": "Land Acquisition",
			"options": "Land Acquisition",
			"read_only": 1,
			"insert_after": "plot",
		},
		{
			"fieldname": "plot_contract",
			"fieldtype": "Link",
			"label": "Plot Contract",
			"options": "Plot Contract",
			"read_only": 1,
			"insert_after": "land_acquisition",
		},
		{
			"fieldname": "is_plot_sale_invoice",
			"fieldtype": "Check",
			"label": "Is Plot Sale Invoice",
			"default": "0",
			"read_only": 1,
			"insert_after": "plot_contract",
		},
	],
}


def ensure_lms_custom_fields():
	create_custom_fields(CUSTOM_FIELDS, update=True)
	frappe.db.commit()
