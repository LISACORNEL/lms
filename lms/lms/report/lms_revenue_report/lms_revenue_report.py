import frappe
from frappe.utils import flt, add_months, today


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	return columns, data, None, chart


def get_columns():
	return [
		{"label": "Period",                "fieldname": "period",          "fieldtype": "Data",    "width": 160},
		{"label": "No. of Payments",       "fieldname": "payment_count",   "fieldtype": "Int",     "width": 140},
		{"label": "Contracts With Payments","fieldname": "contract_count",  "fieldtype": "Int",     "width": 180},
		{"label": "Total Collected (TZS)", "fieldname": "total_collected", "fieldtype": "Float",   "width": 200},
		{"label": "Govt Fee Portion (TZS)","fieldname": "govt_fee",        "fieldtype": "Float",   "width": 190},
		{"label": "Net Revenue (TZS)",     "fieldname": "net_revenue",     "fieldtype": "Float",   "width": 180},
	]


def get_data(filters):
	grouping  = filters.get("grouping")  or "Monthly"
	from_date = filters.get("from_date") or add_months(today(), -6)
	to_date   = filters.get("to_date")   or today()

	if grouping == "Weekly":
		period_expr = "CONCAT('Week ', LPAD(WEEK(pe.posting_date, 1), 2, '0'), ' — ', YEAR(pe.posting_date))"
		sort_expr   = "YEARWEEK(pe.posting_date, 1)"
	else:
		period_expr = "DATE_FORMAT(pe.posting_date, '%%M %%Y')"
		sort_expr   = "DATE_FORMAT(pe.posting_date, '%%Y%%m')"

	rows = frappe.db.sql(f"""
		SELECT
			{period_expr}                                                AS period,
			{sort_expr}                                                  AS sort_key,
			COUNT(DISTINCT pe.name)                                      AS payment_count,
			COUNT(DISTINCT pc.name)                                      AS contract_count,
			SUM(per.allocated_amount)                                    AS total_collected,
			SUM(per.allocated_amount * pc.government_share_percent / 100) AS govt_fee
		FROM `tabPayment Entry` pe
		INNER JOIN `tabPayment Entry Reference` per
			ON  per.parent             = pe.name
			AND per.reference_doctype  = 'Sales Invoice'
		INNER JOIN `tabPlot Contract Payment` pcp
			ON  pcp.sales_invoice = per.reference_name
		INNER JOIN `tabPlot Contract` pc
			ON  pc.name     = pcp.parent
			AND pc.docstatus = 1
		WHERE pe.party_type   = 'Customer'
		  AND pe.docstatus    = 1
		  AND pe.posting_date BETWEEN %(from_date)s AND %(to_date)s
		GROUP BY sort_key, period
		ORDER BY sort_key
	""", {"from_date": from_date, "to_date": to_date}, as_dict=True)

	data = []
	grand_collected = grand_govt = grand_payments = grand_contracts = 0.0

	for row in rows:
		collected = flt(row.total_collected)
		govt      = flt(row.govt_fee)
		net       = collected - govt
		grand_collected  += collected
		grand_govt       += govt
		grand_payments   += row.payment_count
		grand_contracts  += row.contract_count
		data.append({
			"period":          row.period,
			"payment_count":   row.payment_count,
			"contract_count":  row.contract_count,
			"total_collected": collected,
			"govt_fee":        govt,
			"net_revenue":     net,
		})

	if data:
		data.append({
			"period":          "TOTAL",
			"payment_count":   int(grand_payments),
			"contract_count":  int(grand_contracts),
			"total_collected": grand_collected,
			"govt_fee":        grand_govt,
			"net_revenue":     grand_collected - grand_govt,
		})

	return data


def get_chart(data):
	rows = [r for r in data if r.get("period") != "TOTAL"]
	if not rows:
		return None
	return {
		"data": {
			"labels": [r["period"] for r in rows],
			"datasets": [
				{"name": "Total Collected",  "values": [r["total_collected"] for r in rows]},
				{"name": "Net Revenue",      "values": [r["net_revenue"]     for r in rows]},
				{"name": "Govt Fee Portion", "values": [r["govt_fee"]        for r in rows]},
			],
		},
		"type":   "bar",
		"colors": ["#2c5f2e", "#4a9e4d", "#f0a500"],
	}
