import frappe


LMS_DOCTYPES = [
	("lms", "doctype", "lms_settings"),
	("lms", "doctype", "tcb_integration_settings"),
	("lms", "doctype", "tcb_api_log"),
	("lms", "doctype", "land_acquisition_cost_item"),
	("lms", "doctype", "land_acquisition_seller"),
	("lms", "doctype", "land_acquisition"),
	("lms", "doctype", "plot_master"),
	("lms", "doctype", "plot_contract_payment"),
	("lms", "doctype", "plot_application"),
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


def backfill_land_acquisition_sellers():
	"""
	Move older single-seller acquisitions into the new multi-seller child table.
	"""
	if not frappe.db.exists("DocType", "Land Acquisition Seller"):
		return

	names = frappe.get_all(
		"Land Acquisition",
		filters={"seller": ["!=", ""]},
		fields=["name"],
		limit_page_length=0,
		order_by="creation asc",
	)

	for row in names:
		doc = frappe.get_doc("Land Acquisition", row.name)
		if doc.get("land_sellers"):
			continue

		doc.append("land_sellers", {
			"supplier": doc.seller,
			"amount_tzs": doc.seller_purchase_amount_tzs,
			"purchase_invoice": doc.get("purchase_invoice") or "",
		})
		doc.flags.ignore_validate_update_after_submit = True
		doc.save(ignore_permissions=True)

	frappe.db.commit()


def backfill_land_seller_suppliers():
	"""
	Mark legacy land sellers on Supplier so the new PO/PI summary can recognize them.
	"""
	if not frappe.db.has_column("Supplier", "is_land_seller"):
		return

	suppliers = set()

	if frappe.db.has_column("Land Acquisition", "seller"):
		for row in frappe.get_all(
			"Land Acquisition",
			filters={"seller": ["!=", ""]},
			fields=["seller"],
			limit_page_length=0,
		):
			if row.seller:
				suppliers.add(row.seller)

	if frappe.db.exists("DocType", "Land Acquisition Seller"):
		for row in frappe.get_all(
			"Land Acquisition Seller",
			filters={"supplier": ["!=", ""]},
			fields=["supplier"],
			limit_page_length=0,
		):
			if row.supplier:
				suppliers.add(row.supplier)

	for supplier in suppliers:
		if frappe.db.exists("Supplier", supplier):
			frappe.db.set_value("Supplier", supplier, "is_land_seller", 1, update_modified=False)

	frappe.db.commit()


def backfill_supplier_land_seller_details():
	"""
	Move legacy seller identity details from Land Acquisition into Supplier.
	"""
	required_columns = {"seller_id_type", "seller_id_number"}
	if not all(frappe.db.has_column("Supplier", column) for column in required_columns):
		return

	if not all(frappe.db.has_column("Land Acquisition", column) for column in ("seller", *required_columns)):
		return

	rows = frappe.get_all(
		"Land Acquisition",
		filters={"seller": ["!=", ""]},
		fields=["seller", "seller_id_type", "seller_id_number"],
		limit_page_length=0,
	)

	for row in rows:
		if not row.seller or not frappe.db.exists("Supplier", row.seller):
			continue

		current = frappe.db.get_value(
			"Supplier",
			row.seller,
			["seller_id_type", "seller_id_number"],
			as_dict=True,
		) or {}

		updates = {}
		if row.seller_id_type and not current.get("seller_id_type"):
			updates["seller_id_type"] = row.seller_id_type
		if row.seller_id_number and not current.get("seller_id_number"):
			updates["seller_id_number"] = row.seller_id_number

		if updates:
			frappe.db.set_value("Supplier", row.seller, updates, update_modified=False)

	frappe.db.commit()


def hide_supplier_title_deed_field():
	"""
	Hide the temporary Supplier title deed field after moving the source of truth
	back to Land Acquisition.
	"""
	custom_field_name = frappe.db.get_value(
		"Custom Field",
		{"dt": "Supplier", "fieldname": "title_deed_number"},
		"name",
	)
	if not custom_field_name:
		return

	frappe.db.set_value("Custom Field", custom_field_name, "hidden", 1, update_modified=False)
	frappe.clear_cache(doctype="Supplier")
	frappe.db.commit()


def backfill_land_acquisition_plot_rates():
	"""
	Copy the old single selling-rate field into the new per-plot-type rate fields
	for existing acquisitions that have not been updated yet.
	"""
	if not frappe.db.has_column("Land Acquisition", "default_selling_price_per_sqm_tzs"):
		return

	rows = frappe.get_all(
		"Land Acquisition",
		fields=[
			"name",
			"default_selling_price_per_sqm_tzs",
			"residential_selling_price_per_sqm_tzs",
			"commercial_selling_price_per_sqm_tzs",
			"mixed_use_selling_price_per_sqm_tzs",
		],
		limit_page_length=0,
	)

	for row in rows:
		default_rate = frappe.utils.flt(row.default_selling_price_per_sqm_tzs)
		if default_rate <= 0:
			continue

		updates = {}
		if frappe.utils.flt(row.residential_selling_price_per_sqm_tzs) <= 0:
			updates["residential_selling_price_per_sqm_tzs"] = default_rate
		if frappe.utils.flt(row.commercial_selling_price_per_sqm_tzs) <= 0:
			updates["commercial_selling_price_per_sqm_tzs"] = default_rate
		if frappe.utils.flt(row.mixed_use_selling_price_per_sqm_tzs) <= 0:
			updates["mixed_use_selling_price_per_sqm_tzs"] = default_rate

		if updates:
			frappe.db.set_value("Land Acquisition", row.name, updates, update_modified=False)

	frappe.db.commit()
