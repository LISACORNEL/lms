import frappe
from frappe.utils import cstr, flt, today


def validate_payment_entry(doc, method=None):
	"""Normalize LMS plot payments onto the single plot Sales Invoice.

	This makes both manual UI entries and API-created entries behave the same way:
	- if a payment is pointed at the LMS Sales Order, rewrite it to the linked plot SI
	- if it is already pointed at the plot SI, keep it there
	- leave non-LMS references untouched
	"""
	if doc.docstatus != 0:
		return
	if doc.payment_type != "Receive" or doc.party_type != "Customer":
		return
	if not doc.get("references"):
		return

	normalized_rows = []
	seen_invoice_rows: dict[tuple[str, str], dict] = {}

	for row in doc.get("references") or []:
		target = _resolve_lms_plot_reference(row.reference_doctype, row.reference_name)
		if not target:
			normalized_rows.append(_reference_row_to_dict(row))
			continue

		invoice_name = target["invoice_name"]
		payment_term = cstr(getattr(row, "payment_term", "") or "").strip()
		key = (invoice_name, payment_term)
		current_amount = flt(row.allocated_amount)
		existing = seen_invoice_rows.get(key)
		if existing:
			existing["allocated_amount"] = flt(existing.get("allocated_amount")) + current_amount
			existing["outstanding_amount"] = flt(target["outstanding_amount"])
			existing["total_amount"] = flt(target["grand_total"])
			existing["due_date"] = target.get("due_date")
			existing["payment_term_outstanding"] = flt(
				getattr(row, "payment_term_outstanding", 0) or existing.get("payment_term_outstanding") or 0
			)
			existing["exchange_rate"] = flt(getattr(row, "exchange_rate", 0) or existing.get("exchange_rate") or 0) or 1
		else:
			seen_invoice_rows[key] = {
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice_name,
				"allocated_amount": current_amount,
				"total_amount": flt(target["grand_total"]),
				"outstanding_amount": flt(target["outstanding_amount"]),
				"due_date": target.get("due_date"),
				"payment_term": payment_term or None,
				"payment_term_outstanding": flt(getattr(row, "payment_term_outstanding", 0) or 0),
				"exchange_rate": flt(getattr(row, "exchange_rate", 0) or 0) or 1,
			}

	doc.set("references", [])
	for row_dict in normalized_rows:
		doc.append("references", row_dict)
	for row_dict in seen_invoice_rows.values():
		doc.append("references", row_dict)


def on_submit_payment_entry(doc, method=None):
	"""Sync LMS Sales Order and Plot Contract state after payment is posted."""
	_sync_lms_payment_entry_state(doc)


def on_cancel_payment_entry(doc, method=None):
	"""Reverse LMS Sales Order and Plot Contract state after payment cancellation."""
	_sync_lms_payment_entry_state(doc)


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


def _sync_lms_payment_entry_state(doc):
	related = _get_related_lms_documents_from_payment_entry(doc)

	for sales_order_name in sorted(related["sales_orders"]):
		_sync_sales_order_from_plot_invoice(sales_order_name)

	for contract_name in sorted(related["contracts"]):
		if not frappe.db.exists("Plot Contract", contract_name):
			continue
		contract = frappe.get_doc("Plot Contract", contract_name)
		contract.sync_payment_status()


def _get_plot_contracts_from_payment_entry(doc) -> set[str]:
	return _get_related_lms_documents_from_payment_entry(doc)["contracts"]


def _get_related_lms_documents_from_payment_entry(doc) -> dict[str, set[str]]:
	sales_orders = set()
	contracts = set()

	for row in doc.get("references") or []:
		target = _resolve_lms_plot_reference(row.reference_doctype, row.reference_name)
		if not target:
			continue

		if target.get("sales_order_name"):
			sales_orders.add(target["sales_order_name"])
		if target.get("plot_contract"):
			contracts.add(target["plot_contract"])

	return {
		"sales_orders": sales_orders,
		"contracts": contracts,
	}


def _resolve_lms_plot_reference(reference_doctype: str | None, reference_name: str | None):
	if not reference_doctype or not reference_name:
		return None

	if reference_doctype == "Sales Order":
		if not frappe.db.exists("Sales Order", reference_name):
			return None

		so = frappe.db.get_value(
			"Sales Order",
			reference_name,
			["name", "plot_sales_invoice", "plot_contract"],
			as_dict=True,
		)
		invoice_name = _get_plot_invoice_name_from_sales_order(reference_name)
		if not so or not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
			return None

		invoice = frappe.db.get_value(
			"Sales Invoice",
			invoice_name,
			["name", "is_plot_sale_invoice", "grand_total", "outstanding_amount", "due_date", "plot_contract"],
			as_dict=True,
		)
		if not invoice or not invoice.is_plot_sale_invoice:
			return None

		return {
			"sales_order_name": so.name,
			"invoice_name": invoice.name,
			"plot_contract": invoice.plot_contract or so.plot_contract or "",
			"grand_total": invoice.grand_total,
			"outstanding_amount": invoice.outstanding_amount,
			"due_date": invoice.due_date,
		}

	if reference_doctype == "Sales Invoice":
		if not frappe.db.exists("Sales Invoice", reference_name):
			return None

		invoice = frappe.db.get_value(
			"Sales Invoice",
			reference_name,
			["name", "is_plot_sale_invoice", "grand_total", "outstanding_amount", "due_date", "plot_contract"],
			as_dict=True,
		)
		if not invoice or not invoice.is_plot_sale_invoice:
			return None

		sales_order_name = _get_sales_order_name_from_plot_invoice(reference_name)
		return {
			"sales_order_name": sales_order_name or "",
			"invoice_name": invoice.name,
			"plot_contract": invoice.plot_contract
			or frappe.db.get_value("Sales Order", sales_order_name, "plot_contract")
			or "",
			"grand_total": invoice.grand_total,
			"outstanding_amount": invoice.outstanding_amount,
			"due_date": invoice.due_date,
		}

	return None


def _reference_row_to_dict(row):
	return {
		"reference_doctype": row.reference_doctype,
		"reference_name": row.reference_name,
		"allocated_amount": flt(row.allocated_amount),
		"total_amount": flt(getattr(row, "total_amount", 0) or 0),
		"outstanding_amount": flt(getattr(row, "outstanding_amount", 0) or 0),
		"due_date": getattr(row, "due_date", None),
		"payment_term": getattr(row, "payment_term", None),
		"payment_term_outstanding": flt(getattr(row, "payment_term_outstanding", 0) or 0),
		"exchange_rate": flt(getattr(row, "exchange_rate", 0) or 0) or 1,
	}


def _sync_sales_order_from_plot_invoice(sales_order_name: str):
	if not sales_order_name or not frappe.db.exists("Sales Order", sales_order_name):
		return

	so = frappe.get_doc("Sales Order", sales_order_name)
	invoice_name = _get_plot_invoice_name_from_sales_order(sales_order_name)
	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		return

	invoice = frappe.get_doc("Sales Invoice", invoice_name)
	if not getattr(invoice, "is_plot_sale_invoice", 0):
		return

	total_paid = max(0.0, flt(invoice.grand_total) - flt(invoice.outstanding_amount))
	_sync_sales_order_billing_from_plot_invoice(so, invoice)
	_sync_payment_schedule_rows_from_total_paid(invoice, total_paid)
	_sync_payment_schedule_rows_from_total_paid(so, total_paid)

	if so.get("plot_sales_invoice") != invoice.name:
		frappe.db.set_value("Sales Order", so.name, "plot_sales_invoice", invoice.name, update_modified=False)

	frappe.db.set_value(
		"Sales Order",
		so.name,
		{
			"advance_paid": flt(total_paid, so.precision("advance_paid")),
			"plot_outstanding_amount": flt(invoice.outstanding_amount),
		},
		update_modified=False,
	)


def _sync_sales_order_billing_from_plot_invoice(so, invoice):
	if not so.items:
		return

	_link_invoice_items_to_sales_order_rows(so, invoice)
	invoice.reload()
	invoice.update_status_updater_args()
	invoice.update_prevdoc_status()
	so.reload()

	total_amount = sum(abs(flt(row.amount)) for row in so.items)
	billed_amount = sum(
		min(abs(flt(row.amount)), abs(flt(frappe.db.get_value("Sales Order Item", row.name, "billed_amt") or 0)))
		for row in so.items
	)
	per_billed = round((billed_amount / total_amount) * 100, 6) if total_amount else 0.0

	if per_billed < 0.001:
		billing_status = "Not Billed"
	elif per_billed > 99.999999:
		billing_status = "Fully Billed"
	else:
		billing_status = "Partly Billed"

	frappe.db.set_value(
		"Sales Order",
		so.name,
		{
			"per_billed": per_billed,
			"billing_status": billing_status,
		},
		update_modified=False,
	)

	so.reload()
	so.set_status(update=True, update_modified=False)


def _link_invoice_items_to_sales_order_rows(so, invoice):
	so_items = so.get("items") or []
	if not so_items:
		return

	default_so_item = so_items[0].name
	so_item_by_code = {row.item_code: row.name for row in so_items if row.item_code}

	for item in invoice.get("items") or []:
		updates = {}
		if item.sales_order != so.name:
			updates["sales_order"] = so.name

		target_so_detail = item.so_detail or so_item_by_code.get(item.item_code) or default_so_item
		if target_so_detail and item.so_detail != target_so_detail:
			updates["so_detail"] = target_so_detail

		if updates:
			frappe.db.set_value("Sales Invoice Item", item.name, updates, update_modified=False)


def _sync_payment_schedule_rows_from_total_paid(parent_doc, total_paid: float):
	remaining_paid = max(0.0, flt(total_paid))
	conversion_rate = flt(getattr(parent_doc, "conversion_rate", 0) or 0) or 1

	for row in parent_doc.get("payment_schedule") or []:
		payment_amount = flt(row.payment_amount)
		base_payment_amount = flt(row.base_payment_amount) or flt(payment_amount * conversion_rate)
		paid_amount = min(payment_amount, remaining_paid)
		outstanding = max(0.0, payment_amount - paid_amount)
		base_paid_amount = flt(base_payment_amount * (paid_amount / payment_amount)) if payment_amount else 0.0
		base_outstanding = max(0.0, base_payment_amount - base_paid_amount)

		frappe.db.set_value(
			"Payment Schedule",
			row.name,
			{
				"paid_amount": paid_amount,
				"outstanding": outstanding,
				"base_paid_amount": base_paid_amount,
				"base_outstanding": base_outstanding,
			},
			update_modified=False,
		)
		remaining_paid = max(0.0, remaining_paid - paid_amount)


def _get_plot_invoice_name_from_sales_order(sales_order_name: str) -> str:
	invoice_name = frappe.db.get_value("Sales Order", sales_order_name, "plot_sales_invoice")
	if invoice_name and frappe.db.exists("Sales Invoice", invoice_name):
		return invoice_name

	result = frappe.db.sql(
		"""
		select si.name
		from `tabSales Invoice` si
		inner join `tabSales Invoice Item` sii on sii.parent = si.name
		where si.docstatus != 2
		  and si.is_return = 0
		  and ifnull(si.is_plot_sale_invoice, 0) = 1
		  and sii.sales_order = %s
		order by si.modified desc
		limit 1
		""",
		(sales_order_name,),
		as_dict=True,
	)
	return result[0].name if result else ""


def _get_sales_order_name_from_plot_invoice(invoice_name: str) -> str:
	sales_order_name = frappe.db.get_value(
		"Sales Order",
		{"plot_sales_invoice": invoice_name, "docstatus": 1},
		"name",
	)
	if sales_order_name:
		return sales_order_name

	result = frappe.db.sql(
		"""
		select sii.sales_order
		from `tabSales Invoice Item` sii
		where sii.parent = %s
		  and ifnull(sii.sales_order, '') != ''
		order by sii.idx asc
		limit 1
		""",
		(invoice_name,),
		as_dict=True,
	)
	return result[0].sales_order if result else ""


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
