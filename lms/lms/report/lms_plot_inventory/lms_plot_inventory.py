import frappe
from frappe.utils import flt


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data    = get_data(filters)
	summary = get_summary(data)
	return columns, data, None, None, summary


def get_columns():
	return [
		{"label": "Plot",                 "fieldname": "plot",           "fieldtype": "Link",    "options": "Plot Master", "width": 140},
		{"label": "Plot Number",          "fieldname": "plot_number",    "fieldtype": "Data",                              "width": 130},
		{"label": "Plot Type",            "fieldname": "plot_type",      "fieldtype": "Data",                              "width": 120},
		{"label": "Size (sqm)",           "fieldname": "plot_size_sqm",  "fieldtype": "Float",                             "width": 110},
		{"label": "Status",               "fieldname": "status",         "fieldtype": "Data",                              "width": 110},
		{"label": "Allocated Cost (TZS)", "fieldname": "allocated_cost", "fieldtype": "Float",                             "width": 180},
		{"label": "Selling Price (TZS)",  "fieldname": "selling_price",  "fieldtype": "Float",                             "width": 170},
		{"label": "Margin (TZS)",         "fieldname": "margin",         "fieldtype": "Float",                             "width": 150},
		{"label": "Margin %",             "fieldname": "margin_pct",     "fieldtype": "Percent",                           "width": 100},
	]


def get_data(filters):
	conditions = ["pm.docstatus = 1"]
	if filters.get("status"):
		conditions.append("pm.status = %(status)s")
	if filters.get("land_acquisition"):
		conditions.append("pm.land_acquisition = %(land_acquisition)s")
	if filters.get("plot_type"):
		conditions.append("pm.plot_type = %(plot_type)s")

	where = " AND ".join(conditions)

	rows = frappe.db.sql(f"""
		SELECT
			pm.name             AS plot,
			pm.plot_number,
			pm.plot_type,
			pm.plot_size_sqm,
			pm.status,
			pm.land_acquisition,
			pm.allocated_cost,
			pm.selling_price
		FROM `tabPlot Master` pm
		WHERE {where}
		ORDER BY pm.land_acquisition, pm.plot_number
	""", filters, as_dict=True)

	data = []
	for row in rows:
		cost      = flt(row.allocated_cost)
		price     = flt(row.selling_price)
		margin    = price - cost
		margin_pct = (margin / price * 100) if price else 0
		data.append({
			"plot":          row.plot,
			"plot_number":   row.plot_number,
			"plot_type":     row.plot_type,
			"plot_size_sqm": flt(row.plot_size_sqm),
			"status":        row.status,
			"allocated_cost": cost,
			"selling_price":  price,
			"margin":         margin,
			"margin_pct":     margin_pct,
		})
	return data


def get_summary(data):
	if not data:
		return []
	available = sum(1 for r in data if r["status"] == "Available")
	reserved  = sum(1 for r in data if r["status"] == "Reserved")
	delivered = sum(1 for r in data if r["status"] == "Delivered")
	return [
		{"label": "Total Plots",   "value": len(data),   "datatype": "Int",   "indicator": "Blue"},
		{"label": "Available",     "value": available,   "datatype": "Int",   "indicator": "Green"},
		{"label": "Reserved",      "value": reserved,    "datatype": "Int",   "indicator": "Yellow"},
		{"label": "Delivered",     "value": delivered,   "datatype": "Int",   "indicator": "Grey"},
	]
