import frappe


LMS_DOCTYPES = [
	("lms", "doctype", "lms_settings"),
	("lms", "doctype", "land_acquisition_cost_item"),
	("lms", "doctype", "land_acquisition"),
	("lms", "doctype", "plot_master"),
	("lms", "doctype", "plot_contract_payment"),
	("lms", "doctype", "plot_application"),
	("lms", "doctype", "plot_sales_order"),
	("lms", "doctype", "plot_contract"),
	("lms", "doctype", "plot_handover"),
]


def reload_lms_doctypes():
	"""
	Called after every bench migrate via after_migrate hook in hooks.py.
	Forces Frappe to sync LMS DocTypes from their JSON definitions.
	This prevents them from being marked as orphaned and deleted during migration.
	"""
	for module, doc_type, doctype_name in LMS_DOCTYPES:
		try:
			frappe.reload_doc(module, doc_type, doctype_name, force=True)
		except Exception as e:
			frappe.log_error(
				title=f"LMS migrate: failed to reload {doctype_name}",
				message=str(e)
			)

	frappe.db.commit()
