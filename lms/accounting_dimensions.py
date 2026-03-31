import frappe
from frappe.exceptions import DuplicateEntryError


DIMENSION_DOCTYPE = "Land Acquisition"
DIMENSION_LABEL = "Land Acquisition"
TARGET_TABLES = [
	("Purchase Order", "items", "Purchase Order Item"),
	("Purchase Invoice", "items", "Purchase Invoice Item"),
	("Journal Entry", "accounts", "Journal Entry Account"),
]


def _get_dimension_doc():
	name = frappe.db.get_value("Accounting Dimension", {"document_type": DIMENSION_DOCTYPE}, "name")
	if name:
		return frappe.get_doc("Accounting Dimension", name)

	doc = frappe.get_doc(
		{
			"doctype": "Accounting Dimension",
			"label": DIMENSION_LABEL,
			"document_type": DIMENSION_DOCTYPE,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
		return doc
	except DuplicateEntryError:
		name = frappe.db.get_value("Accounting Dimension", {"document_type": DIMENSION_DOCTYPE}, "name")
		if not name:
			raise
		return frappe.get_doc("Accounting Dimension", name)


def _sync_dimension_fields():
	from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
		create_accounting_dimensions_for_doctype,
		get_doctypes_with_dimensions,
	)

	for doctype in get_doctypes_with_dimensions():
		create_accounting_dimensions_for_doctype(doctype)


def ensure_land_acquisition_accounting_dimension():
	doc = _get_dimension_doc()
	if doc.disabled:
		doc.db_set("disabled", 0, update_modified=False)
		frappe.flags.accounting_dimensions = None
		frappe.flags.accounting_dimensions_details = None

	_sync_dimension_fields()
	return doc.name


def _get_project_mapping():
	if not frappe.db.has_column("Land Acquisition", "project"):
		return {}

	rows = frappe.db.sql(
		"""
		select name, project
		from `tabLand Acquisition`
		where ifnull(project, '') != ''
		""",
		as_dict=True,
	)
	return {row.project: row.name for row in rows if row.project}


def _backfill_child_rows_from_project(parent_doctype, table_field, child_doctype, project_map):
	if not frappe.db.has_column(child_doctype, "land_acquisition") or not frappe.db.has_column(
		child_doctype, "project"
	):
		return 0

	updated = 0
	fields = ["name", "parent", "project", "land_acquisition"]
	rows = frappe.get_all(child_doctype, fields=fields, filters={"docstatus": ["!=", 2]})

	for row in rows:
		land_acquisition = row.land_acquisition or project_map.get(row.project)
		if not land_acquisition:
			continue
		if row.land_acquisition == land_acquisition:
			continue

		frappe.db.set_value(child_doctype, row.name, "land_acquisition", land_acquisition, update_modified=False)
		updated += 1

		if frappe.db.has_column(parent_doctype, "land_acquisition"):
			parent_land = frappe.db.get_value(parent_doctype, row.parent, "land_acquisition")
			if not parent_land:
				frappe.db.set_value(
					parent_doctype, row.parent, "land_acquisition", land_acquisition, update_modified=False
				)

	return updated


def _backfill_gl_entries_from_project(project_map):
	if not frappe.db.has_column("GL Entry", "land_acquisition") or not frappe.db.has_column("GL Entry", "project"):
		return 0

	updated = 0
	rows = frappe.get_all(
		"GL Entry",
		fields=["name", "project", "land_acquisition"],
		filters={"is_cancelled": 0},
	)
	for row in rows:
		land_acquisition = row.land_acquisition or project_map.get(row.project)
		if not land_acquisition:
			continue
		if row.land_acquisition == land_acquisition:
			continue

		frappe.db.set_value("GL Entry", row.name, "land_acquisition", land_acquisition, update_modified=False)
		updated += 1

	return updated


def backfill_land_acquisition_dimension_from_project():
	project_map = _get_project_mapping()
	if not project_map:
		return {"updated": 0}

	ensure_land_acquisition_accounting_dimension()

	updated = 0
	for parent_doctype, table_field, child_doctype in TARGET_TABLES:
		updated += _backfill_child_rows_from_project(parent_doctype, table_field, child_doctype, project_map)

	updated += _backfill_gl_entries_from_project(project_map)
	frappe.db.commit()
	return {"updated": updated}


def prepare_land_acquisition_dimension_migration():
	ensure_land_acquisition_accounting_dimension()
	backfill_land_acquisition_dimension_from_project()
