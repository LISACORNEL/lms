import frappe
from frappe.utils import add_days, cint, flt, getdate, today

from lms.lms.doctype.plot_master.plot_master import PLOT_TYPE_TO_ITEM
from lms.lms.tcb import decline_reference_for_sales_order, generate_control_number, register_reference_for_sales_order


def validate_sales_order(doc, method=None):
	if not _is_lms_sales_order(doc):
		return

	settings = frappe.get_single("LMS Settings")
	application = _get_application(doc)
	plot = _get_plot(doc)

	doc.company = doc.company or settings.company
	doc.customer = application.customer
	doc.plot = plot.name
	doc.land_acquisition = plot.land_acquisition
	doc.acquisition_name = plot.acquisition_name
	doc.booking_fee_percent = flt(plot.booking_fee_percent)
	doc.government_share_percent = flt(plot.government_share_percent)
	doc.payment_completion_days = cint(plot.payment_completion_days)
	doc.transaction_date = doc.transaction_date or application.payment_date or today()
	doc.payment_deadline = add_days(doc.transaction_date, cint(doc.payment_completion_days or 0))
	doc.set_warehouse = doc.set_warehouse or settings.plot_inventory_warehouse
	doc.ignore_default_payment_terms_template = 1

	_validate_application_window(application)
	_validate_plot_state(plot)
	_ensure_single_sales_order_for_application(doc, application)
	_ensure_items(doc, plot, settings)
	_ensure_payment_schedule(doc, plot)


@frappe.whitelist()
def get_sales_order_defaults(plot_application: str):
	"""Return LMS Sales Order defaults for a selected paid Plot Application."""
	if not plot_application:
		return {}

	if not frappe.db.exists("Plot Application", plot_application):
		frappe.throw(f"Plot Application {plot_application} was not found.")

	application = frappe.get_doc("Plot Application", plot_application)
	if application.docstatus != 1 or application.status != "Paid":
		frappe.throw("Only submitted, fee-paid Plot Applications can be used to create a Sales Order.")

	if application.sales_order and frappe.db.exists("Sales Order", application.sales_order):
		frappe.throw(
			f"Plot Application {application.name} is already linked to Sales Order {application.sales_order}."
		)

	if not application.plot or not frappe.db.exists("Plot Master", application.plot):
		frappe.throw(f"Plot Application {application.name} is missing a valid plot.")

	settings = frappe.get_single("LMS Settings")
	plot = frappe.get_doc("Plot Master", application.plot)
	_validate_application_window(application)
	_validate_plot_state(plot)

	item_code = PLOT_TYPE_TO_ITEM.get(plot.plot_type)
	if not item_code:
		frappe.throw(f"No item is mapped for plot type {plot.plot_type}.")

	transaction_date = application.payment_date or today()
	payment_completion_days = cint(plot.payment_completion_days or 0)
	payment_deadline = add_days(transaction_date, payment_completion_days)
	payment_schedule = _build_payment_schedule_rows(
		total_amount=flt(plot.selling_price),
		booking_fee_percent=flt(plot.booking_fee_percent),
		transaction_date=transaction_date,
		payment_deadline=payment_deadline,
	)
	item_row = _build_sales_order_item_row(plot, settings.plot_inventory_warehouse, payment_deadline)

	return {
		"company": settings.company,
		"customer": application.customer,
		"plot": plot.name,
		"land_acquisition": plot.land_acquisition,
		"acquisition_name": plot.acquisition_name,
		"booking_fee_percent": flt(plot.booking_fee_percent),
		"government_share_percent": flt(plot.government_share_percent),
		"payment_completion_days": payment_completion_days,
		"transaction_date": transaction_date,
		"payment_deadline": payment_deadline,
		"set_warehouse": settings.plot_inventory_warehouse,
		"item": item_row,
		"payment_schedule": payment_schedule,
	}


def on_submit_sales_order(doc, method=None):
	if not _is_lms_sales_order(doc):
		return

	control_number = doc.control_number or generate_control_number(doc.name)
	if doc.control_number != control_number:
		doc.db_set("control_number", control_number)
		doc.control_number = control_number

	application = _get_application(doc)
	if application.sales_order != doc.name:
		application.db_set("sales_order", doc.name)

	if frappe.db.get_value("Plot Master", doc.plot, "status") == "Available":
		frappe.db.set_value("Plot Master", doc.plot, "status", "Pending Advance")

	registration = register_reference_for_sales_order(doc.name, control_number)
	if not registration.get("ok"):
		frappe.throw(registration.get("message") or "TCB reference registration failed.")

	contract_name = _ensure_draft_plot_contract(doc)
	invoice_name = _ensure_plot_sales_invoice(doc, contract_name)

	if contract_name and doc.plot_contract != contract_name:
		doc.db_set("plot_contract", contract_name)
	if invoice_name and doc.plot_sales_invoice != invoice_name:
		doc.db_set("plot_sales_invoice", invoice_name)

	if invoice_name:
		from lms.payment_sync import _sync_sales_order_from_plot_invoice

		_sync_sales_order_from_plot_invoice(doc.name)


def on_cancel_sales_order(doc, method=None):
	if not _is_lms_sales_order(doc):
		return

	application = _get_application(doc, required=False)
	if application and application.sales_order == doc.name:
		application.db_set("sales_order", "")

	_cancel_unpaid_plot_sales_invoice(doc)
	_delete_draft_plot_contract(doc)

	if doc.control_number:
		decline_reference_for_sales_order(doc.name, doc.control_number)

	plot_status = frappe.db.get_value("Plot Master", doc.plot, "status")
	if plot_status == "Pending Advance":
		frappe.db.set_value("Plot Master", doc.plot, "status", "Available")


def _is_lms_sales_order(doc) -> bool:
	return bool(doc.get("plot_application") or doc.get("plot"))


def _get_application(doc, required=True):
	if not doc.get("plot_application"):
		if required:
			frappe.throw("Plot Application is required for LMS Sales Orders.")
		return None

	if not frappe.db.exists("Plot Application", doc.plot_application):
		if required:
			frappe.throw(f"Plot Application {doc.plot_application} was not found.")
		return None

	app = frappe.get_doc("Plot Application", doc.plot_application)
	if required and (app.docstatus != 1 or app.status not in ("Paid", "Converted")):
		frappe.throw(
			f"Plot Application {app.name} must be submitted and fee-paid before creating a Sales Order."
		)
	return app


def _get_plot(doc):
	if not doc.get("plot"):
		frappe.throw("Plot is required for LMS Sales Orders.")
	if not frappe.db.exists("Plot Master", doc.plot):
		frappe.throw(f"Plot {doc.plot} was not found.")
	return frappe.get_doc("Plot Master", doc.plot)


def _validate_application_window(application):
	if application.expiry_date and getdate(application.expiry_date) < getdate(today()):
		frappe.throw(
			f"Plot Application {application.name} has expired. The fee-validity window has ended."
		)


def _validate_plot_state(plot):
	if plot.status not in ("Pending Advance", "Available"):
		frappe.throw(
			f"Plot {plot.name} is not ready for Sales Order creation (current status: {plot.status})."
		)


def _ensure_single_sales_order_for_application(doc, application):
	existing = frappe.db.get_value(
		"Sales Order",
		{
			"plot_application": application.name,
			"name": ("!=", doc.name),
			"docstatus": ("!=", 2),
		},
		"name",
	)
	if existing:
		frappe.throw(
			f"Plot Application {application.name} is already linked to Sales Order {existing}."
		)


def _ensure_items(doc, plot, settings):
	delivery_date = add_days(doc.transaction_date or today(), cint(doc.payment_completion_days or 0))
	row_values = _build_sales_order_item_row(plot, settings.plot_inventory_warehouse, delivery_date)

	if len(doc.items or []) == 1 and doc.items[0].get("item_code") == row_values["item_code"]:
		row = doc.items[0]
		for key, value in row_values.items():
			row.set(key, value)
		return

	doc.set("items", [row_values])


def _build_sales_order_item_row(plot, warehouse, delivery_date):
	item_code = PLOT_TYPE_TO_ITEM.get(plot.plot_type)
	if not item_code:
		frappe.throw(f"No item is mapped for plot type {plot.plot_type}.")

	item = frappe.db.get_value(
		"Item",
		item_code,
		["name", "item_name", "stock_uom"],
		as_dict=True,
	)
	if not item:
		frappe.throw(f"Item {item_code} was not found.")
	if not item.stock_uom:
		frappe.throw(f"Item {item_code} is missing Stock UOM.")

	return {
		"item_code": item.name,
		"item_name": item.item_name,
		"uom": item.stock_uom,
		"stock_uom": item.stock_uom,
		"conversion_factor": 1,
		"qty": 1,
		"rate": flt(plot.selling_price),
		"warehouse": warehouse,
		"delivery_date": delivery_date,
	}


def _build_payment_schedule_rows(total_amount, booking_fee_percent, transaction_date, payment_deadline):
	total_amount = flt(total_amount)
	booking_fee_percent = max(0.0, min(100.0, flt(booking_fee_percent)))
	transaction_date = transaction_date or today()
	payment_deadline = payment_deadline or transaction_date

	if total_amount <= 0:
		return []

	if booking_fee_percent <= 0:
		return [
			{
				"description": "Full Plot Payment",
				"due_date": payment_deadline,
				"invoice_portion": 100.0,
				"payment_amount": total_amount,
			}
		]

	booking_amount = flt(total_amount * booking_fee_percent / 100)
	balance_amount = flt(total_amount - booking_amount)
	rows = [
		{
			"description": "Booking Fee",
			"due_date": transaction_date,
			"invoice_portion": booking_fee_percent,
			"payment_amount": booking_amount,
		}
	]

	if balance_amount > 0:
		rows.append(
			{
				"description": "Balance",
				"due_date": payment_deadline,
				"invoice_portion": flt(100 - booking_fee_percent),
				"payment_amount": balance_amount,
			}
		)

	return rows


def _ensure_payment_schedule(doc, plot):
	schedule_rows = _build_payment_schedule_rows(
		total_amount=flt(plot.selling_price),
		booking_fee_percent=flt(doc.booking_fee_percent),
		transaction_date=doc.transaction_date,
		payment_deadline=doc.payment_deadline,
	)

	if not schedule_rows:
		doc.set("payment_schedule", [])
		return

	doc.set("payment_schedule", [])
	for row in schedule_rows:
		doc.append("payment_schedule", row)


def _ensure_draft_plot_contract(doc):
	if doc.get("plot_contract") and frappe.db.exists("Plot Contract", doc.plot_contract):
		contract = frappe.get_doc("Plot Contract", doc.plot_contract)
		if contract.docstatus == 0:
			_sync_contract_schedule(contract, doc)
			contract.save(ignore_permissions=True)
		return contract.name

	existing = frappe.db.get_value(
		"Plot Contract",
		{"sales_order": doc.name, "docstatus": ("!=", 2)},
		"name",
	)
	if existing:
		contract = frappe.get_doc("Plot Contract", existing)
		if contract.docstatus == 0:
			_sync_contract_schedule(contract, doc)
			contract.save(ignore_permissions=True)
		return existing

	contract = frappe.get_doc(
		{
			"doctype": "Plot Contract",
			"customer": doc.customer,
			"plot": doc.plot,
			"contract_date": doc.transaction_date or today(),
			"payment_completion_days": cint(doc.payment_completion_days or 0),
			"sales_order": doc.name,
			"booking_fee_percent": flt(doc.booking_fee_percent),
			"government_share_percent": flt(doc.government_share_percent),
			"notes": doc.terms or "",
		}
	)
	_sync_contract_schedule(contract, doc)
	contract.flags.from_sales_order = True
	contract.insert(ignore_permissions=True)
	return contract.name


def _sync_contract_schedule(contract, doc):
	contract.set("payment_schedule", [])
	for idx, row in enumerate(doc.get("payment_schedule") or [], start=1):
		contract.append(
			"payment_schedule",
			{
				"installment_number": idx,
				"due_date": row.due_date,
				"expected_amount": flt(row.payment_amount),
				"paid_amount": 0,
				"status": "Pending",
			},
		)


def _ensure_plot_sales_invoice(doc, contract_name):
	if doc.get("plot_sales_invoice") and frappe.db.exists("Sales Invoice", doc.plot_sales_invoice):
		_link_plot_invoice_to_sales_order(doc, doc.plot_sales_invoice)
		return doc.plot_sales_invoice

	settings = frappe.get_single("LMS Settings")
	plot = _get_plot(doc)
	item_code = PLOT_TYPE_TO_ITEM.get(plot.plot_type)
	if not item_code:
		frappe.throw(f"No item is mapped for plot type {plot.plot_type}.")

	existing = frappe.db.get_value(
		"Sales Invoice",
		{
			"plot": doc.plot,
			"is_plot_sale_invoice": 1,
			"is_return": 0,
			"docstatus": ("!=", 2),
		},
		"name",
	)
	if existing:
		_link_plot_invoice_to_sales_order(doc, existing)
		return existing

	invoice = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"customer": doc.customer,
			"posting_date": doc.transaction_date or today(),
			"due_date": doc.payment_deadline or add_days(doc.transaction_date or today(), cint(doc.payment_completion_days or 0)),
			"ignore_default_payment_terms_template": 1,
			"company": doc.company or settings.company,
			"plot": doc.plot,
			"land_acquisition": doc.land_acquisition,
			"plot_contract": contract_name or "",
			"is_plot_sale_invoice": 1,
			"remarks": f"Plot sale invoice for {doc.plot} via Sales Order {doc.name}",
				"items": [
					{
						"item_code": item_code,
						"qty": 1,
						"rate": flt(plot.selling_price),
						"income_account": settings.customer_advance_account,
						"sales_order": doc.name,
						"so_detail": doc.items[0].name if doc.items else "",
						"description": f"Plot sale for {doc.plot}",
					}
				],
			"payment_schedule": [
				{
					"description": row.description,
					"due_date": row.due_date,
					"invoice_portion": flt(row.invoice_portion),
					"payment_amount": flt(row.payment_amount),
				}
				for row in (doc.get("payment_schedule") or [])
			],
		}
	)
	invoice.insert(ignore_permissions=True)
	invoice.submit()
	return invoice.name


def _link_plot_invoice_to_sales_order(doc, invoice_name):
	"""Backfill missing Sales Order item linkage on existing plot invoices."""
	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		return
	if not doc.items:
		return

	so_item_name = doc.items[0].name
	if not so_item_name:
		return

	invoice_items = frappe.get_all(
		"Sales Invoice Item",
		filters={"parent": invoice_name},
		fields=["name", "sales_order", "so_detail"],
	)
	for item in invoice_items:
		updates = {}
		if item.sales_order != doc.name:
			updates["sales_order"] = doc.name
		if not item.so_detail:
			updates["so_detail"] = so_item_name
		if updates:
			frappe.db.set_value("Sales Invoice Item", item.name, updates, update_modified=False)


def _cancel_unpaid_plot_sales_invoice(doc):
	invoice_name = doc.get("plot_sales_invoice")
	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		return

	invoice = frappe.get_doc("Sales Invoice", invoice_name)
	if invoice.docstatus != 1:
		return

	if flt(invoice.outstanding_amount) < flt(invoice.grand_total):
		frappe.throw(
			f"Sales Order {doc.name} cannot be cancelled because plot invoice {invoice.name} has payments."
		)

	invoice.cancel()


def _delete_draft_plot_contract(doc):
	contract_name = doc.get("plot_contract")
	if not contract_name or not frappe.db.exists("Plot Contract", contract_name):
		return

	contract = frappe.get_doc("Plot Contract", contract_name)
	if contract.docstatus == 0:
		frappe.delete_doc("Plot Contract", contract.name, ignore_permissions=True)
