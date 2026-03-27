"""
LMS data reset helpers.

Run via:
    bench --site lms.com execute lms.lms.cleanup.clear_lms_data
"""

import frappe


def clear_lms_data():
	"""Delete LMS flow data and linked ERP transactions.

	Keeps:
	  - LMS Settings / TCB Integration Settings
	  - Company, Chart of Accounts, Users, Roles
	  - Item masters, Customers, Suppliers

	Deletes:
	  - TCB API Log
	  - Plot Handovers and linked Delivery Notes
	  - Plot Applications and their fee SI / Payment Entry
	  - ERP Sales Orders created by LMS and their plot Sales Invoices
	  - Plot Contracts and their tracking rows
	  - Contract/Acquisition Journal Entries created by LMS
	  - Plot Masters, linked Stock Entries, Serial Nos
	  - Land Acquisitions
	"""
	data = _collect_reset_targets()

	_log(f"Reset scope: applications={len(data['applications'])}, sales_orders={len(data['sales_orders'])}, "
	     f"contracts={len(data['contracts'])}, handovers={len(data['handovers'])}, plots={len(data['plots'])}, "
	     f"land_acquisitions={len(data['land_acquisitions'])}")
	_log(f"Linked ERP docs: payment_entries={len(data['payment_entries'])}, sales_invoices={len(data['sales_invoices'])}, "
	     f"journal_entries={len(data['journal_entries'])}, delivery_notes={len(data['delivery_notes'])}, "
	     f"stock_entries={len(data['stock_entries'])}, serial_nos={len(data['serial_nos'])}, "
	     f"tcb_logs={len(data['tcb_logs'])}")

	# 1. Cancel/delete handovers first so linked Delivery Notes are reversed cleanly.
	for name in data["handovers"]:
		_cancel_and_delete("Plot Handover", name)

	# 2. Remove payment entries before invoices.
	for name in data["payment_entries"]:
		_cancel_and_delete("Payment Entry", name)

	# 3. Remove ERP Sales Orders created by LMS while their control numbers/invoice links still exist.
	for name in data["sales_orders"]:
		_cancel_and_delete("Sales Order", name)

	# 4. Remove Plot Applications after linked Sales Orders are gone.
	for name in data["applications"]:
		_cancel_and_delete("Plot Application", name)

	# 5. Clear references that would otherwise block invoice / JE deletion.
	_clear_link_fields(data)

	# 6. Remove sales invoices after payments and LMS parents are gone.
	for name in data["sales_invoices"]:
		_cancel_and_delete("Sales Invoice", name)

	# 7. Remove contract/acquisition journal entries after references are cleared.
	for name in data["journal_entries"]:
		_cancel_and_delete("Journal Entry", name)

	# 8. Remove TCB logs.
	for name in data["tcb_logs"]:
		_delete_doc_force("TCB API Log", name)

	# 9. Remove contracts and their child rows.
	if frappe.db.exists("DocType", "Plot Contract Payment"):
		frappe.db.sql("DELETE FROM `tabPlot Contract Payment`")
		frappe.db.commit()

	for name in data["contracts"]:
		_cancel_and_delete("Plot Contract", name)

	# 10. Remove plots so their stock receipts are reversed, then clean stock artifacts.
	for name in data["plots"]:
		_cancel_and_delete("Plot Master", name)

	# 11. Clean any ERP stock docs still left behind.
	for name in data["delivery_notes"]:
		if frappe.db.exists("Delivery Note", name):
			_cancel_and_delete("Delivery Note", name)

	for name in data["stock_entries"]:
		if frappe.db.exists("Stock Entry", name):
			_cancel_and_delete("Stock Entry", name)

	for name in data["serial_nos"]:
		if frappe.db.exists("Serial No", name):
			_delete_doc_force("Serial No", name)

	# 12. Remove Land Acquisitions last.
	for name in data["land_acquisitions"]:
		_cancel_and_delete("Land Acquisition", name)

	# 13. Sweep older orphaned ERP docs that still match LMS naming/remark patterns.
	for name in _get_residual_delivery_notes():
		_cancel_and_delete("Delivery Note", name)

	for name in _get_residual_stock_entries():
		_cancel_and_delete("Stock Entry", name)

	for name in _get_residual_serial_nos():
		_delete_doc_force("Serial No", name)

	for name in _get_residual_journal_entries():
		_cancel_and_delete("Journal Entry", name)

	frappe.db.commit()
	_log("Done — LMS flow data cleared.")


def _collect_reset_targets():
	applications = frappe.get_all(
		"Plot Application",
		fields=["name", "sales_invoice", "payment_entry", "sales_order"],
		limit_page_length=0,
		order_by="creation asc",
	)
	sales_orders = frappe.get_all(
		"Sales Order",
		filters={
			"plot": ["!=", ""],
		},
		fields=["name", "plot_sales_invoice", "plot_contract"],
		limit_page_length=0,
		order_by="creation asc",
	)
	contracts = frappe.get_all(
		"Plot Contract",
		fields=["name", "booking_fee_invoice", "government_fee_entry", "forfeiture_entry"],
		limit_page_length=0,
		order_by="creation asc",
	)
	handovers = frappe.get_all(
		"Plot Handover",
		fields=["name", "delivery_note"],
		limit_page_length=0,
		order_by="creation asc",
	)
	plots = frappe.get_all(
		"Plot Master",
		fields=["name", "stock_entry", "serial_no"],
		limit_page_length=0,
		order_by="creation asc",
	)
	land_acquisitions = frappe.get_all(
		"Land Acquisition",
		fields=["name", "journal_entry"],
		limit_page_length=0,
		order_by="creation asc",
	)
	tcb_logs = frappe.get_all("TCB API Log", pluck="name", limit_page_length=0)

	sales_invoice_names = {
		row.sales_invoice for row in applications if row.get("sales_invoice")
	}
	sales_invoice_names.update(
		row.plot_sales_invoice for row in sales_orders if row.get("plot_sales_invoice")
	)
	sales_invoice_names.update(
		row.booking_fee_invoice for row in contracts if row.get("booking_fee_invoice")
	)
	if frappe.db.exists("DocType", "Plot Contract Payment"):
		sales_invoice_names.update(
			frappe.db.sql(
				"""
				select distinct sales_invoice
				from `tabPlot Contract Payment`
				where ifnull(sales_invoice, '') != ''
				""",
				pluck=True,
			)
			or []
		)

	payment_entry_names = {
		row.payment_entry for row in applications if row.get("payment_entry")
	}
	if sales_invoice_names:
		placeholders = ", ".join(["%s"] * len(sales_invoice_names))
		payment_entry_names.update(
			frappe.db.sql(
				f"""
				select distinct parent
				from `tabPayment Entry Reference`
				where reference_doctype = 'Sales Invoice'
				  and reference_name in ({placeholders})
				""",
				tuple(sales_invoice_names),
				pluck=True,
			)
			or []
		)

	journal_entry_names = {
		row.government_fee_entry for row in contracts if row.get("government_fee_entry")
	}
	journal_entry_names.update(
		row.forfeiture_entry for row in contracts if row.get("forfeiture_entry")
	)
	journal_entry_names.update(
		row.journal_entry for row in land_acquisitions if row.get("journal_entry")
	)

	delivery_note_names = {
		row.delivery_note for row in handovers if row.get("delivery_note")
	}
	stock_entry_names = {
		row.stock_entry for row in plots if row.get("stock_entry")
	}
	serial_no_names = {
		row.serial_no for row in plots if row.get("serial_no")
	}

	return {
		"applications": [row.name for row in applications],
		"sales_orders": [row.name for row in sales_orders],
		"contracts": [row.name for row in contracts],
		"handovers": [row.name for row in handovers],
		"plots": [row.name for row in plots],
		"land_acquisitions": [row.name for row in land_acquisitions],
		"payment_entries": sorted(name for name in payment_entry_names if name),
		"sales_invoices": sorted(name for name in sales_invoice_names if name),
		"journal_entries": sorted(name for name in journal_entry_names if name),
		"delivery_notes": sorted(name for name in delivery_note_names if name),
		"stock_entries": sorted(name for name in stock_entry_names if name),
		"serial_nos": sorted(name for name in serial_no_names if name),
		"tcb_logs": sorted(name for name in tcb_logs if name),
	}


def _clear_link_fields(data):
	if frappe.db.exists("DocType", "Plot Contract Payment"):
		frappe.db.sql("update `tabPlot Contract Payment` set sales_invoice = ''")

	if data["contracts"]:
		frappe.db.sql(
			"""
			update `tabPlot Contract`
			set booking_fee_invoice = '',
			    government_fee_entry = '',
			    forfeiture_entry = ''
			"""
		)

	if data["land_acquisitions"] and frappe.db.has_column("Land Acquisition", "journal_entry"):
		frappe.db.sql("update `tabLand Acquisition` set journal_entry = ''")

	frappe.db.commit()


def _delete_doc_force(doctype, name):
	try:
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()
	except Exception as exc:
		_log(f"WARN: could not delete {doctype} {name}: {exc}")


def _cancel_and_delete(doctype, name):
	if not frappe.db.exists(doctype, name):
		return
	try:
		doc = frappe.get_doc(doctype, name)
		if doc.docstatus == 1:
			try:
				doc.flags.ignore_links = True
				doc.flags.ignore_permissions = True
				doc.cancel()
			except Exception:
				frappe.db.set_value(doctype, name, "docstatus", 2)
				frappe.db.commit()
		_delete_doc_force(doctype, name)
	except Exception as exc:
		_log(f"WARN: could not delete {doctype} {name}: {exc}")


def _log(message):
	print(message)
	frappe.logger("lms").info(message)


def _get_residual_delivery_notes():
	rows = frappe.get_all(
		"Delivery Note Item",
		filters={"serial_no": ["like", "PLT-%"]},
		fields=["parent"],
		group_by="parent",
		limit_page_length=0,
	)
	return [row.parent for row in rows if row.get("parent")]


def _get_residual_stock_entries():
	rows = frappe.get_all(
		"Stock Entry",
		filters={"remarks": ["like", "Plot % from %"]},
		fields=["name"],
		limit_page_length=0,
	)
	return [row.name for row in rows if row.get("name")]


def _get_residual_serial_nos():
	rows = frappe.get_all(
		"Serial No",
		filters={"name": ["like", "PLT-%"]},
		fields=["name"],
		limit_page_length=0,
	)
	return [row.name for row in rows if row.get("name")]


def _get_residual_journal_entries():
	names = set()
	patterns = (
		("Land Acquisition — %",),
		("Revenue recognition — Contract %",),
		("Contract termination — funds forfeited %",),
	)
	for (pattern,) in patterns:
		rows = frappe.get_all(
			"Journal Entry",
			filters={"user_remark": ["like", pattern]},
			fields=["name"],
			limit_page_length=0,
		)
		names.update(row.name for row in rows if row.get("name"))
	return sorted(names)
