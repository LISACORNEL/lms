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
		{"label": "Contract",           "fieldname": "contract",            "fieldtype": "Link",    "options": "Plot Contract", "width": 150},
		{"label": "Customer",           "fieldname": "customer",            "fieldtype": "Link",    "options": "Customer",      "width": 190},
		{"label": "Plot",               "fieldname": "plot",                "fieldtype": "Link",    "options": "Plot Master",   "width": 130},
		{"label": "Contract Date",      "fieldname": "contract_date",       "fieldtype": "Date",                                "width": 120},
		{"label": "Deadline",           "fieldname": "payment_deadline",    "fieldtype": "Date",                                "width": 120},
		{"label": "Status",             "fieldname": "contract_status",     "fieldtype": "Data",                                "width": 100},
		{"label": "Contract Value (TZS)","fieldname": "selling_price",      "fieldtype": "Float",                               "width": 170},
		{"label": "Paid (TZS)",         "fieldname": "total_paid",          "fieldtype": "Float",                               "width": 150},
		{"label": "Outstanding (TZS)",  "fieldname": "total_outstanding",   "fieldtype": "Float",                               "width": 160},
		{"label": "Progress %",         "fieldname": "progress_pct",        "fieldtype": "Percent",                             "width": 110},
		{"label": "Installments",       "fieldname": "installment_summary", "fieldtype": "Data",                                "width": 140},
	]


def get_data(filters):
	conditions = ["pc.docstatus = 1"]

	if filters.get("contract_status"):
		conditions.append("pc.contract_status = %(contract_status)s")
	else:
		conditions.append("pc.contract_status IN ('Ongoing', 'Completed')")

	if filters.get("customer"):
		conditions.append("pc.customer = %(customer)s")
	if filters.get("from_date"):
		conditions.append("pc.contract_date >= %(from_date)s")
	if filters.get("to_date"):
		conditions.append("pc.contract_date <= %(to_date)s")

	where = " AND ".join(conditions)

	rows = frappe.db.sql(f"""
		SELECT
			pc.name                                                              AS contract,
			pc.customer,
			pc.plot,
			pc.contract_date,
			pc.payment_deadline,
			pc.contract_status,
			pc.selling_price,
			pc.total_paid,
			pc.total_outstanding,
			COUNT(pcp.name)                                                      AS total_inst,
			SUM(CASE WHEN pcp.status = 'Paid'    THEN 1 ELSE 0 END)             AS paid_inst,
			SUM(CASE WHEN pcp.status = 'Overdue' THEN 1 ELSE 0 END)             AS overdue_inst
		FROM `tabPlot Contract` pc
		LEFT JOIN `tabPlot Contract Payment` pcp ON pcp.parent = pc.name
		WHERE {where}
		GROUP BY pc.name
		ORDER BY pc.contract_date DESC
	""", filters, as_dict=True)

	data = []
	for row in rows:
		price = flt(row.selling_price)
		paid  = flt(row.total_paid)
		pct   = (paid / price * 100) if price else 0
		inst_summary = f"{row.paid_inst or 0}/{row.total_inst or 0}"
		if row.overdue_inst:
			inst_summary += f" ({row.overdue_inst} overdue)"
		data.append({
			"contract":             row.contract,
			"customer":             row.customer,
			"plot":                 row.plot,
			"contract_date":        row.contract_date,
			"payment_deadline":     row.payment_deadline,
			"contract_status":      row.contract_status,
			"selling_price":        price,
			"total_paid":           paid,
			"total_outstanding":    flt(row.total_outstanding),
			"progress_pct":         pct,
			"installment_summary":  inst_summary,
		})
	return data


def get_summary(data):
	if not data:
		return []
	total_value       = sum(flt(r["selling_price"])     for r in data)
	total_paid        = sum(flt(r["total_paid"])        for r in data)
	total_outstanding = sum(flt(r["total_outstanding"]) for r in data)
	return [
		{"label": "Total Contract Value", "value": total_value,       "datatype": "Float", "indicator": "Blue"},
		{"label": "Total Paid",           "value": total_paid,        "datatype": "Float", "indicator": "Green"},
		{"label": "Total Outstanding",    "value": total_outstanding, "datatype": "Float", "indicator": "Red"},
	]
