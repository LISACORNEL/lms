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
		{"label": "Contract",           "fieldname": "contract",          "fieldtype": "Link",    "options": "Plot Contract", "width": 150},
		{"label": "Customer",           "fieldname": "customer",          "fieldtype": "Link",    "options": "Customer",      "width": 190},
		{"label": "Plot",               "fieldname": "plot",              "fieldtype": "Link",    "options": "Plot Master",   "width": 130},
		{"label": "Recognition Date",   "fieldname": "recognition_date",  "fieldtype": "Date",                                "width": 150},
		{"label": "Revenue (TZS)",      "fieldname": "revenue",           "fieldtype": "Float",                               "width": 170},
		{"label": "COGS (TZS)",         "fieldname": "cogs",              "fieldtype": "Float",                               "width": 160},
		{"label": "Gross Margin (TZS)", "fieldname": "gross_margin",      "fieldtype": "Float",                               "width": 170},
		{"label": "Margin %",           "fieldname": "margin_pct",        "fieldtype": "Percent",                             "width": 100},
	]


def get_data(filters):
	conditions = [
		"pc.docstatus = 1",
		"pc.contract_status = 'Completed'",
	]
	if filters.get("from_date"):
		conditions.append("je.posting_date >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append("je.posting_date <= %(to_date)s")
	if filters.get("customer"):
		conditions.append("pc.customer = %(customer)s")

	where = " AND ".join(conditions)

	rows = frappe.db.sql(f"""
		SELECT
			pc.name                  AS contract,
			pc.customer,
			pc.plot,
			je.posting_date          AS recognition_date,
			pc.selling_price,
			pc.government_fee_withheld,
			pm.allocated_cost
		FROM `tabPlot Contract` pc
		INNER JOIN `tabPlot Master` pm ON pm.name = pc.plot
		LEFT JOIN `tabJournal Entry` je ON je.name = pc.government_fee_entry
		WHERE {where}
		ORDER BY je.posting_date DESC
	""", filters, as_dict=True)

	data = []
	for row in rows:
		price     = flt(row.selling_price)
		govt_fee  = flt(row.government_fee_withheld)
		cogs      = flt(row.allocated_cost)
		revenue   = price - govt_fee
		margin    = revenue - cogs
		margin_pct = (margin / revenue * 100) if revenue else 0
		data.append({
			"contract":         row.contract,
			"customer":         row.customer,
			"plot":             row.plot,
			"recognition_date": row.recognition_date,
			"revenue":          revenue,
			"cogs":             cogs,
			"gross_margin":     margin,
			"margin_pct":       margin_pct,
		})
	return data


def get_summary(data):
	if not data:
		return []
	total_revenue = sum(flt(r["revenue"])      for r in data)
	total_cogs    = sum(flt(r["cogs"])         for r in data)
	total_margin  = sum(flt(r["gross_margin"]) for r in data)
	avg_margin    = (total_margin / total_revenue * 100) if total_revenue else 0
	return [
		{"label": "Total Revenue Recognized", "value": total_revenue,          "datatype": "Float", "indicator": "Blue"},
		{"label": "Total COGS",               "value": total_cogs,             "datatype": "Float", "indicator": "Red"},
		{"label": "Total Gross Margin",       "value": total_margin,           "datatype": "Float", "indicator": "Green"},
		{"label": "Average Margin %",         "value": round(avg_margin, 1),   "datatype": "Float", "indicator": "Blue"},
	]
