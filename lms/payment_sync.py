import frappe
from frappe.utils import cstr, flt, today


def on_submit_payment_entry(doc, method=None):
	"""Sync LMS contract state after a plot-sale payment is posted."""
	for contract_name in _get_plot_contracts_from_payment_entry(doc):
		if not frappe.db.exists("Plot Contract", contract_name):
			continue
		contract = frappe.get_doc("Plot Contract", contract_name)
		contract.sync_payment_status()


def create_payment_entry_for_sales_order(
	*,
	sales_order_name: str,
	amount: float,
	payment_date: str | None,
	bank_account: str,
	reference_no: str | None = None,
	remarks: str | None = None,
) -> str:
	"""Create and submit a Payment Entry against the single LMS plot invoice."""
	so = frappe.get_doc("Sales Order", sales_order_name)
	if so.docstatus != 1:
		frappe.throw(f"Sales Order {so.name} must be submitted before receiving payment.")

	invoice_name = so.get("plot_sales_invoice")
	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		frappe.throw(f"Sales Order {so.name} is missing its plot Sales Invoice.")

	invoice = frappe.get_doc("Sales Invoice", invoice_name)
	if not getattr(invoice, "is_plot_sale_invoice", 0):
		frappe.throw(f"Sales Invoice {invoice.name} is not marked as an LMS plot sale invoice.")
	if invoice.docstatus != 1:
		frappe.throw(f"Sales Invoice {invoice.name} must be submitted before receiving payment.")

	amount = flt(amount)
	if amount <= 0:
		frappe.throw("Payment amount must be greater than zero.")

	outstanding = flt(invoice.outstanding_amount)
	if outstanding <= 0:
		frappe.throw(f"Sales Invoice {invoice.name} is already fully paid.")
	if amount > outstanding:
		frappe.throw(
			f"Payment amount exceeds the outstanding balance on {invoice.name} by TZS {amount - outstanding:,.0f}."
		)

	reference_no = cstr(reference_no or "").strip()
	if reference_no and _payment_reference_exists(invoice.name, reference_no):
		frappe.throw(f"Duplicate payment reference '{reference_no}' for Sales Invoice {invoice.name}.")

	_validate_bank_account(bank_account, so.company)

	pe = frappe.get_doc(
		{
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"posting_date": payment_date or today(),
			"company": so.company,
			"party_type": "Customer",
			"party": so.customer,
			"paid_from": invoice.debit_to,
			"paid_to": bank_account,
			"paid_amount": amount,
			"received_amount": amount,
			"reference_no": reference_no or so.name,
			"reference_date": payment_date or today(),
			"remarks": remarks or f"Plot payment for {so.name} / Plot {so.plot}",
			"references": [
				{
					"reference_doctype": "Sales Invoice",
					"reference_name": invoice.name,
					"allocated_amount": amount,
				}
			],
		}
	)
	pe.insert(ignore_permissions=True)
	pe.submit()
	return pe.name


def _get_plot_contracts_from_payment_entry(doc) -> set[str]:
	contract_names = set()
	for row in doc.get("references") or []:
		if row.reference_doctype != "Sales Invoice" or not row.reference_name:
			continue

		invoice_name = row.reference_name
		if not frappe.db.exists("Sales Invoice", invoice_name):
			continue

		invoice = frappe.db.get_value(
			"Sales Invoice",
			invoice_name,
			["is_plot_sale_invoice", "plot_contract"],
			as_dict=True,
		)
		if not invoice or not invoice.is_plot_sale_invoice:
			continue

		contract_name = invoice.plot_contract or frappe.db.get_value(
			"Sales Order",
			{"plot_sales_invoice": invoice_name, "docstatus": 1},
			"plot_contract",
		)
		if contract_name:
			contract_names.add(contract_name)

	return contract_names


def _payment_reference_exists(invoice_name: str, reference_no: str) -> bool:
	existing = frappe.db.sql(
		"""
		select pe.name
		from `tabPayment Entry` pe
		inner join `tabPayment Entry Reference` per
			on per.parent = pe.name
		where pe.docstatus = 1
		  and pe.reference_no = %s
		  and per.reference_doctype = 'Sales Invoice'
		  and per.reference_name = %s
		limit 1
		""",
		(reference_no, invoice_name),
		as_dict=True,
	)
	return bool(existing)


def _validate_bank_account(account, company):
	account_info = frappe.db.get_value(
		"Account",
		account,
		["name", "company", "account_type", "is_group"],
		as_dict=True,
	)
	if not account_info:
		frappe.throw(f"Bank account {account} was not found.")
	if account_info.is_group:
		frappe.throw(f"{account} is a group account. Please choose a posting bank account.")
	if account_info.account_type != "Bank":
		frappe.throw(f"{account} is not a Bank account.")
	if account_info.company and account_info.company != company:
		frappe.throw(
			f"Bank account {account} belongs to company {account_info.company}, not {company}."
		)
