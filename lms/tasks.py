import frappe
from frappe.utils import today, getdate, flt


def daily():
	"""Entry point for all LMS daily scheduled jobs."""
	submit_due_installment_invoices()
	mark_overdue_installments()


def submit_due_installment_invoices():
	"""Submit Draft installment Sales Invoices whose due date has arrived.

	On the due date each installment SI is submitted so it:
	  - Appears in the customer's outstanding invoices
	  - Feeds the Accounts Receivable ageing report
	  - Can receive a Payment Entry via Record Payment
	"""
	today_date = getdate(today())

	due_rows = frappe.db.get_all(
		"Plot Contract Payment",
		filters={
			"due_date": ["<=", today_date],
			"sales_invoice": ["!=", ""],
			"status": ["!=", "Paid"],
		},
		fields=["name", "parent", "sales_invoice", "due_date", "installment_number"],
	)

	affected_contracts = set()

	for row in due_rows:
		si_docstatus = frappe.db.get_value("Sales Invoice", row.sales_invoice, "docstatus")
		if si_docstatus != 0:
			continue  # Already submitted or cancelled — skip

		# Only process contracts that are still Active
		contract_status = frappe.db.get_value("Plot Contract", row.parent, "contract_status")
		if contract_status != "Active":
			continue

		try:
			si_doc = frappe.get_doc("Sales Invoice", row.sales_invoice)
			si_doc.submit()
			affected_contracts.add(row.parent)
			frappe.logger().info(
				f"LMS daily: submitted SI {row.sales_invoice} for "
				f"installment #{row.installment_number} of contract {row.parent} "
				f"(due {row.due_date})"
			)
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"LMS: Failed to auto-submit SI {row.sales_invoice} "
				f"for contract {row.parent}",
			)

	# Commit before syncing so the submitted SI state is visible in sync queries
	frappe.db.commit()

	for contract_name in affected_contracts:
		try:
			contract = frappe.get_doc("Plot Contract", contract_name)
			contract.sync_payment_status()
		except Exception:
			frappe.log_error(
				frappe.get_traceback(),
				f"LMS: Failed to sync payment status for {contract_name}",
			)


def mark_overdue_installments():
	"""Flip Pending installment rows to Overdue once their due date has passed.

	Runs after submit_due_installment_invoices so newly submitted SIs
	are included in the overdue scan.
	"""
	today_date = getdate(today())

	pending_past_due = frappe.db.get_all(
		"Plot Contract Payment",
		filters={
			"due_date": ["<", today_date],
			"status": "Pending",
		},
		fields=["name", "sales_invoice"],
	)

	for row in pending_past_due:
		if not row.sales_invoice:
			continue

		si_info = frappe.db.get_value(
			"Sales Invoice",
			row.sales_invoice,
			["docstatus", "outstanding_amount"],
			as_dict=True,
		)
		if not si_info:
			continue

		# Only mark Overdue if SI is submitted AND still has outstanding balance
		if si_info.docstatus == 1 and flt(si_info.outstanding_amount) > 0:
			frappe.db.set_value("Plot Contract Payment", row.name, "status", "Overdue")
