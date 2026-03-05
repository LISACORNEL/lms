"""
LMS Test Data Cleanup
Run via: bench --site lms.com execute lms.lms.cleanup.clear_lms_data
"""

import frappe


def clear_lms_data():
	"""Delete all LMS transactional data.

	Keeps: company, chart of accounts, users, customers, LMS Settings.
	Deletes: Plot Contracts, Plot Masters, Land Acquisitions, Plot Handovers,
	         and all linked Sales Invoices, Payment Entries, Journal Entries.
	"""

	# ── 1. Collect linked accounting doc names ────────────────────────────

	# Journal Entries created by contracts (completion + termination)
	je_names = set()
	for row in frappe.db.sql("""
		SELECT government_fee_entry, forfeiture_entry
		FROM `tabPlot Contract`
		WHERE COALESCE(government_fee_entry, '') != ''
		   OR COALESCE(forfeiture_entry, '') != ''
	""", as_dict=True):
		if row.government_fee_entry:
			je_names.add(row.government_fee_entry)
		if row.forfeiture_entry:
			je_names.add(row.forfeiture_entry)

	# Sales Invoices from payment schedule rows
	si_names = set(frappe.db.sql("""
		SELECT DISTINCT sales_invoice
		FROM `tabPlot Contract Payment`
		WHERE COALESCE(sales_invoice, '') != ''
	""", pluck=True) or [])

	# Also booking fee invoices stored on the contract header
	si_names.update(frappe.db.sql("""
		SELECT DISTINCT booking_fee_invoice
		FROM `tabPlot Contract`
		WHERE COALESCE(booking_fee_invoice, '') != ''
	""", pluck=True) or [])

	# Payment Entries that reference any of those invoices
	pe_names = set()
	if si_names:
		placeholders = ", ".join(["%s"] * len(si_names))
		pe_names.update(frappe.db.sql(
			f"""
			SELECT DISTINCT parent
			FROM `tabPayment Entry Reference`
			WHERE reference_doctype = 'Sales Invoice'
			  AND reference_name IN ({placeholders})
			""",
			tuple(si_names),
			pluck=True,
		) or [])

	# ── 2. Cancel & delete Payment Entries ───────────────────────────────
	_log(f"Deleting {len(pe_names)} Payment Entries …")
	for name in pe_names:
		_cancel_and_delete("Payment Entry", name)

	# ── 3. Clear SI links on child rows, then cancel & delete SIs ────────
	_log(f"Deleting {len(si_names)} Sales Invoices …")
	if si_names:
		frappe.db.sql("""
			UPDATE `tabPlot Contract Payment`
			SET sales_invoice = ''
			WHERE sales_invoice IN ({})
		""".format(", ".join([f"'{s}'" for s in si_names])))
		frappe.db.sql("""
			UPDATE `tabPlot Contract`
			SET booking_fee_invoice = ''
			WHERE booking_fee_invoice IN ({})
		""".format(", ".join([f"'{s}'" for s in si_names])))
		frappe.db.commit()

	for name in si_names:
		_cancel_and_delete("Sales Invoice", name)

	# ── 4. Cancel & delete Journal Entries ───────────────────────────────
	_log(f"Deleting {len(je_names)} Journal Entries …")
	# Clear references on contracts first
	frappe.db.sql("UPDATE `tabPlot Contract` SET government_fee_entry = '', forfeiture_entry = ''")
	frappe.db.commit()
	for name in je_names:
		_cancel_and_delete("Journal Entry", name)

	# ── 5. Delete Plot Handovers ──────────────────────────────────────────
	handovers = frappe.get_all("Plot Handover", pluck="name")
	_log(f"Deleting {len(handovers)} Plot Handovers …")
	for name in handovers:
		_cancel_and_delete("Plot Handover", name)

	# ── 6. Delete Plot Contracts (clear children first) ───────────────────
	contracts = frappe.get_all("Plot Contract", pluck="name")
	_log(f"Deleting {len(contracts)} Plot Contracts …")
	frappe.db.sql("DELETE FROM `tabPlot Contract Payment`")
	frappe.db.commit()
	for name in contracts:
		_cancel_and_delete("Plot Contract", name)

	# ── 7. Delete Plot Masters ────────────────────────────────────────────
	plots = frappe.get_all("Plot Master", pluck="name")
	_log(f"Deleting {len(plots)} Plot Masters …")
	for name in plots:
		_cancel_and_delete("Plot Master", name)

	# ── 8. Delete Land Acquisitions ───────────────────────────────────────
	las = frappe.get_all("Land Acquisition", pluck="name")
	_log(f"Deleting {len(las)} Land Acquisitions …")
	for name in las:
		_cancel_and_delete("Land Acquisition", name)

	_log("Done — LMS data cleared. Company, accounts, users and LMS Settings are untouched.")


# ── helpers ───────────────────────────────────────────────────────────────────

def _log(msg):
	print(msg)
	frappe.log_error(msg, "LMS Cleanup")


def _cancel_and_delete(doctype, name):
	try:
		doc = frappe.get_doc(doctype, name)
		if doc.docstatus == 1:
			try:
				doc.flags.ignore_links = True
				doc.cancel()
			except Exception:
				# If normal cancel fails, force docstatus=2 in DB directly
				frappe.db.set_value(doctype, name, "docstatus", 2)
				frappe.db.commit()
		frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
		frappe.db.commit()
	except Exception as e:
		_log(f"  WARN: could not delete {doctype} {name}: {e}")
