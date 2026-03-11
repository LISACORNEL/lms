import frappe
from frappe.utils import flt


def execute(filters=None):
	columns = get_columns()
	data    = get_data()
	return columns, data


def get_columns():
	return [
		{"label": "KPI",   "fieldname": "kpi",   "fieldtype": "Data", "width": 320},
		{"label": "Value", "fieldname": "value",  "fieldtype": "Data", "width": 220},
		{"label": "Notes", "fieldname": "notes",  "fieldtype": "Data", "width": 350},
	]


def get_data():
	# ── Plot counts ──────────────────────────────────────────────────────
	plot_counts = frappe.db.sql("""
		SELECT status, COUNT(name) AS cnt
		FROM `tabPlot Master`
		WHERE docstatus = 1
		GROUP BY status
	""", as_dict=True)
	plot_map   = {r.status: r.cnt for r in plot_counts}
	available  = plot_map.get("Available",    0)
	reserved   = plot_map.get("Reserved",     0)
	delivered  = plot_map.get("Delivered",    0)
	total_plots = sum(plot_map.values())

	# ── Contract financials ───────────────────────────────────────────────
	fin = frappe.db.sql("""
		SELECT
			SUM(total_paid)                                                                    AS cash_collected,
			SUM(CASE WHEN contract_status = 'Ongoing' THEN total_paid  ELSE 0 END) AS deferred_revenue,
			SUM(CASE WHEN contract_status =  'Completed' THEN selling_price        ELSE 0 END) AS recognized_gross,
			SUM(CASE WHEN contract_status =  'Completed' THEN government_fee_withheld ELSE 0 END) AS govt_fees,
			SUM(CASE WHEN contract_status IN ('Ongoing','Completed') THEN selling_price ELSE 0 END) AS active_pipeline
		FROM `tabPlot Contract`
		WHERE docstatus = 1
		  AND contract_status IN ('Ongoing','Completed')
	""", as_dict=True)[0]

	# ── COGS on completed plots ─────────────────────────────────────────
	cogs_row = frappe.db.sql("""
		SELECT SUM(pm.allocated_cost) AS total_cogs
		FROM `tabPlot Contract` pc
		INNER JOIN `tabPlot Master` pm ON pm.name = pc.plot
		WHERE pc.docstatus = 1 AND pc.contract_status = 'Completed'
	""", as_dict=True)[0]

	cash_collected  = flt(fin.cash_collected)
	deferred        = flt(fin.deferred_revenue)
	active_pipeline = flt(fin.active_pipeline)
	recognized_gross = flt(fin.recognized_gross)
	govt_fees       = flt(fin.govt_fees)
	revenue_recog   = recognized_gross - govt_fees
	cogs            = flt(cogs_row.total_cogs)
	gross_margin    = revenue_recog - cogs
	margin_pct      = (gross_margin / revenue_recog * 100) if revenue_recog else 0
	collection_rate = (cash_collected / active_pipeline * 100) if active_pipeline else 0

	def tzs(n):  return f"TZS {n:,.0f}"
	def pct(n):  return f"{n:.1f}%"

	return [
		{"kpi": "── PLOTS ──────────────────",      "value": "",                    "notes": ""},
		{"kpi": "Available Plots",                   "value": str(available),        "notes": "Ready to be contracted"},
		{"kpi": "Reserved (Under Contract)",         "value": str(reserved),         "notes": "Ongoing contracts"},
		{"kpi": "Delivered Plots",                   "value": str(delivered),        "notes": "Fully paid and handed over"},
		{"kpi": "Total Plots in System",             "value": str(total_plots),      "notes": "All submitted plots"},
		{"kpi": "",                                  "value": "",                    "notes": ""},
		{"kpi": "── FINANCIALS ─────────────",       "value": "",                    "notes": ""},
		{"kpi": "Cash Collected",                    "value": tzs(cash_collected),   "notes": "All payments received to date"},
		{"kpi": "Deferred Revenue (Liability)",      "value": tzs(deferred),         "notes": "Advances collected on undelivered plots"},
		{"kpi": "Revenue Recognized",                "value": tzs(revenue_recog),    "notes": "Completed contracts net of govt fee"},
		{"kpi": "Government Fees Payable",           "value": tzs(govt_fees),        "notes": "Obligation on completed contracts"},
		{"kpi": "COGS (Delivered Plots)",            "value": tzs(cogs),             "notes": "Allocated land cost for delivered plots"},
		{"kpi": "Gross Margin",                      "value": tzs(gross_margin),     "notes": "Revenue Recognized − COGS"},
		{"kpi": "",                                  "value": "",                    "notes": ""},
		{"kpi": "── RATIOS ──────────────────",      "value": "",                    "notes": ""},
		{"kpi": "Gross Margin %",                    "value": pct(margin_pct),       "notes": "Gross Margin ÷ Revenue Recognized"},
		{"kpi": "Collection Rate %",                 "value": pct(collection_rate),  "notes": "Cash Collected ÷ Ongoing+Completed Pipeline"},
	]
