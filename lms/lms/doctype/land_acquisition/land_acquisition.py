import frappe
from frappe.model.document import Document
from frappe.utils import today, flt, cint


@frappe.whitelist()
def sync_land_acquisition_plot_summary(land_acquisition):
    """Recompute and persist submitted Plot Master counts for one Land Acquisition.

    Uses docstatus=1 plots only (active inventory records).
    Also toggles status Approved <-> Subdivided based on whether any plots exist.
    """
    if not land_acquisition or not frappe.db.exists("Land Acquisition", land_acquisition):
        return {}

    rows = frappe.db.get_all(
        "Plot Master",
        filters={
            "land_acquisition": land_acquisition,
            "docstatus": 1,
        },
        fields=["status", "count(name) as cnt"],
        group_by="status",
    )
    status_map = {row.status: int(row.cnt or 0) for row in rows}

    total_plots = int(sum(status_map.values()))
    summary = {
        "total_plots": total_plots,
        "available_plots": int(status_map.get("Available", 0)),
        "reserved_plots": int(
            status_map.get("Pending Fee", 0)
            + status_map.get("Pending Advance", 0)
            + status_map.get("Reserved", 0)
            + status_map.get("Ready for Handover", 0)
        ),
        "delivered_plots": int(status_map.get("Delivered", 0) + status_map.get("Title Closed", 0)),
    }

    la_state = frappe.db.get_value(
        "Land Acquisition",
        land_acquisition,
        ["status", "docstatus"],
        as_dict=True,
    )
    if la_state and la_state.docstatus == 1:
        if total_plots > 0 and la_state.status == "Approved":
            summary["status"] = "Subdivided"
        elif total_plots == 0 and la_state.status == "Subdivided":
            summary["status"] = "Approved"

    frappe.db.set_value("Land Acquisition", land_acquisition, summary, update_modified=False)
    return summary


def validate_coordinate_pair(doc, latitude_field="latitude", longitude_field="longitude"):
	latitude = doc.get(latitude_field)
	longitude = doc.get(longitude_field)
	has_latitude = latitude not in (None, "")
	has_longitude = longitude not in (None, "")

	if has_latitude != has_longitude:
		frappe.throw("Enter both Latitude and Longitude, or leave both blank.")

	if not has_latitude:
		return

	latitude = flt(latitude)
	longitude = flt(longitude)
	if latitude < -90 or latitude > 90:
		frappe.throw("Latitude must be between -90 and 90.")
	if longitude < -180 or longitude > 180:
		frappe.throw("Longitude must be between -180 and 180.")


class LandAcquisition(Document):

	def after_insert(self):
		sync_land_acquisition_cost_summary(self.name)

	def validate(self):
		self.set_cost_defaults()
		self.calculate_cost_summary()
		self.validate_cost()
		self.validate_area()
		self.validate_sales_defaults()
		validate_coordinate_pair(self)

	def before_submit(self):
		if self.approval_state != "Approved":
			frappe.throw("Land Acquisition must be approved through workflow before submission.")

	def set_cost_defaults(self):
		company = frappe.db.get_single_value("LMS Settings", "company")
		default_currency = frappe.db.get_value("Company", company, "default_currency") if company else None
		self.acquisition_currency = default_currency or self.acquisition_currency or "TZS"
		self.exchange_rate = 1.0

	def calculate_cost_summary(self):
		summary = get_land_acquisition_cost_summary(self)
		self.seller_purchase_amount_tzs = flt(summary.get("seller_purchase_amount_tzs"))
		self.committed_cost_tzs = flt(summary.get("committed_cost_tzs"))
		self.additional_project_cost_tzs = flt(summary.get("additional_project_cost_tzs"))
		self.acquisition_cost_tzs = flt(summary.get("acquisition_cost_tzs"))
		self.cost_per_sqm_tzs = flt(summary.get("cost_per_sqm_tzs"))
		self.total_acquisition_cost = flt(summary.get("total_acquisition_cost"))
		self.supplier_invoice_total_tzs = flt(summary.get("supplier_invoice_total_tzs"))

	def validate_cost(self):
		if flt(self.seller_purchase_amount_tzs) < 0:
			frappe.throw("Base Seller Cost (TZS) cannot be negative.")

	def validate_area(self):
		if flt(self.total_area_sqm) <= 0:
			frappe.throw("Total Area must be greater than zero.")

	def validate_sales_defaults(self):
		if flt(self.booking_fee_percent) < 0 or flt(self.booking_fee_percent) > 100:
			frappe.throw("Booking Fee % must be between 0 and 100.")

		if flt(self.government_share_percent) < 0 or flt(self.government_share_percent) > 100:
			frappe.throw("Government Share % must be between 0 and 100.")

		if cint(self.payment_completion_days) <= 0:
			frappe.throw("Payment Completion Days must be greater than zero.")

		for label, fieldname in (
			("Residential Rate per sqm (TZS)", "residential_selling_price_per_sqm_tzs"),
			("Commercial Rate per sqm (TZS)", "commercial_selling_price_per_sqm_tzs"),
			("Mixed-Use Rate per sqm (TZS)", "mixed_use_selling_price_per_sqm_tzs"),
		):
			if flt(self.get(fieldname)) < 0:
				frappe.throw(f"{label} cannot be negative.")

	def on_submit(self):
		sync_land_acquisition_cost_summary(self.name)
		self.db_set("status", "Approved")
		self.db_set("approved_by", frappe.session.user)
		self.db_set("approval_date", today())
		sync_land_acquisition_plot_summary(self.name)

	def before_cancel(self):
		"""Block cancellation while submitted Plot Masters still exist."""
		active_plot_count = frappe.db.count("Plot Master", {
			"land_acquisition": self.name,
			"docstatus": 1,
		})
		if not active_plot_count:
			return

		sample_plots = frappe.db.get_all(
			"Plot Master",
			filters={"land_acquisition": self.name, "docstatus": 1},
			fields=["name"],
			limit_page_length=3,
		)
		sample_names = ", ".join(row.name for row in sample_plots)
		extra = ""
		if active_plot_count > len(sample_plots):
			extra = f", and {active_plot_count - len(sample_plots)} more"

		frappe.throw(
			"Cannot cancel this Land Acquisition because submitted plots still exist: "
			f"{sample_names}{extra}. Cancel those Plot Master records first."
		)

	def on_cancel(self):
		self.cancel_purchase_posting()
		sync_land_acquisition_cost_summary(self.name)
		sync_land_acquisition_plot_summary(self.name)

	def cancel_purchase_posting(self):
		"""Cancel only legacy auto-created postings from older seller flows."""
		if self.purchase_invoice and frappe.db.exists("Purchase Invoice", self.purchase_invoice):
			pi_doc = frappe.get_doc("Purchase Invoice", self.purchase_invoice)
			if pi_doc.docstatus == 1:
				pi_doc.cancel()
			elif pi_doc.docstatus == 0:
				frappe.delete_doc("Purchase Invoice", pi_doc.name, ignore_permissions=True)
			return

		# Legacy fallback for older Land Acquisition records created before the PI cutover.
		if not self.journal_entry or not frappe.db.exists("Journal Entry", self.journal_entry):
			return

		je_doc = frappe.get_doc("Journal Entry", self.journal_entry)
		if je_doc.docstatus == 1:
			je_doc.cancel()
		elif je_doc.docstatus == 0:
			frappe.delete_doc("Journal Entry", je_doc.name, ignore_permissions=True)


@frappe.whitelist()
def sync_land_acquisition_cost_summary(land_acquisition):
	if not land_acquisition or not frappe.db.exists("Land Acquisition", land_acquisition):
		return {}

	doc = frappe.get_doc("Land Acquisition", land_acquisition)
	doc.set_cost_defaults()
	summary = get_land_acquisition_cost_summary(doc)
	persisted_summary = {
		"acquisition_currency": summary.get("acquisition_currency") or "TZS",
		"exchange_rate": flt(summary.get("exchange_rate") or 1.0),
		"seller_purchase_amount_tzs": flt(summary.get("seller_purchase_amount_tzs")),
		"committed_cost_tzs": flt(summary.get("committed_cost_tzs")),
		"additional_project_cost_tzs": flt(summary.get("additional_project_cost_tzs")),
		"acquisition_cost_tzs": flt(summary.get("acquisition_cost_tzs")),
		"cost_per_sqm_tzs": flt(summary.get("cost_per_sqm_tzs")),
		"total_acquisition_cost": flt(summary.get("total_acquisition_cost")),
		"supplier_invoice_total_tzs": flt(summary.get("supplier_invoice_total_tzs")),
	}
	frappe.db.set_value(
		"Land Acquisition",
		land_acquisition,
		persisted_summary,
		update_modified=False,
	)
	return summary


def get_land_acquisition_cost_summary(doc_or_name):
	doc = (
		doc_or_name
		if isinstance(doc_or_name, LandAcquisition)
		else frappe.get_doc("Land Acquisition", doc_or_name)
	)

	land_account = frappe.db.get_single_value("LMS Settings", "land_under_development_account")

	actual_posted_cost = 0.0
	seller_posted_cost = 0.0
	committed_cost = 0.0
	seller_po_rows = []
	seller_pi_rows = []
	supplier_po_rows = []
	supplier_pi_rows = []
	supplier_invoice_rows = []
	supplier_invoice_total = 0.0
	land_seller_rows = []
	other_supplier_rows = []

	if frappe.db.has_column("Purchase Order Item", "land_acquisition"):
		seller_po_rows = frappe.db.sql(
			"""
			select
				coalesce(nullif(po.supplier_name, ''), po.supplier) as supplier_name,
				po.supplier,
				%s as title_deed_number,
				supplier.seller_id_type as seller_id_type,
				supplier.seller_id_number as seller_id_number,
				po.name as purchase_order,
				sum(poi.base_net_amount) as amount_tzs,
				'Purchase Order' as source_doctype,
				po.status as status_label,
				po.transaction_date as posting_date
			from `tabPurchase Order Item` poi
			inner join `tabPurchase Order` po on po.name = poi.parent
			inner join `tabSupplier` supplier on supplier.name = po.supplier
			where poi.land_acquisition = %s
			  and po.docstatus = 1
			  and ifnull(supplier.is_land_seller, 0) = 1
			group by po.name, po.supplier, po.supplier_name, po.status, po.transaction_date
			having abs(sum(poi.base_net_amount)) > 0.0001
			order by po.transaction_date desc, po.creation desc
			""",
			(doc.title_deed_number or "", doc.name),
			as_dict=True,
		)
		for row in seller_po_rows:
			row.amount_tzs = flt(row.amount_tzs)

		supplier_po_rows = frappe.db.sql(
			"""
			select
				coalesce(nullif(po.supplier_name, ''), po.supplier) as supplier_name,
				po.name as purchase_order,
				sum(poi.base_net_amount) as amount_tzs,
				'Purchase Order' as source_doctype,
				po.status as status_label,
				po.transaction_date as posting_date
			from `tabPurchase Order Item` poi
			inner join `tabPurchase Order` po on po.name = poi.parent
			inner join `tabSupplier` supplier on supplier.name = po.supplier
			where poi.land_acquisition = %s
			  and po.docstatus = 1
			  and ifnull(supplier.is_land_seller, 0) = 0
			group by po.name, po.supplier, po.supplier_name, po.status, po.transaction_date
			having abs(sum(poi.base_net_amount)) > 0.0001
			order by po.transaction_date desc, po.creation desc
			""",
			(doc.name,),
			as_dict=True,
		)
		for row in supplier_po_rows:
			row.amount_tzs = flt(row.amount_tzs)

	if land_account and frappe.db.has_column("GL Entry", "land_acquisition"):
		actual_posted_cost = flt(
			frappe.db.sql(
				"""
				select ifnull(sum(debit - credit), 0)
				from `tabGL Entry`
				where land_acquisition = %s
				  and voucher_type = 'Purchase Invoice'
				  and account = %s
				  and ifnull(is_cancelled, 0) = 0
				""",
				(doc.name, land_account),
				)[0][0]
				or 0
			)

		seller_pi_rows = frappe.db.sql(
			"""
			select
				coalesce(nullif(pi.supplier_name, ''), pi.supplier) as supplier_name,
				pi.supplier,
				%s as title_deed_number,
				supplier.seller_id_type as seller_id_type,
				supplier.seller_id_number as seller_id_number,
				pii.purchase_orders,
				gl.voucher_no as purchase_invoice,
				sum(gl.debit - gl.credit) as amount_tzs,
				'Purchase Invoice' as source_doctype,
				'Posted' as status_label,
				pi.posting_date as posting_date
			from `tabGL Entry` gl
			inner join `tabPurchase Invoice` pi on pi.name = gl.voucher_no
			inner join `tabSupplier` supplier on supplier.name = pi.supplier
			left join (
				select
					parent,
					group_concat(distinct purchase_order order by purchase_order separator ', ') as purchase_orders
				from `tabPurchase Invoice Item`
				where land_acquisition = %s
				group by parent
			) pii on pii.parent = pi.name
			where gl.voucher_type = 'Purchase Invoice'
			  and gl.land_acquisition = %s
			  and gl.account = %s
			  and ifnull(gl.is_cancelled, 0) = 0
			  and pi.docstatus = 1
			  and ifnull(supplier.is_land_seller, 0) = 1
			group by gl.voucher_no, pi.supplier, pi.supplier_name, pi.posting_date
			having abs(sum(gl.debit - gl.credit)) > 0.0001
			order by pi.posting_date desc, pi.creation desc
			""",
			(doc.title_deed_number or "", doc.name, doc.name, land_account),
			as_dict=True,
		)
		for row in seller_pi_rows:
			row.amount_tzs = flt(row.amount_tzs)

		if seller_pi_rows:
			seller_posted_cost = sum(flt(row.amount_tzs) for row in seller_pi_rows)
		elif doc.get("purchase_invoice") and frappe.db.exists("Purchase Invoice", doc.purchase_invoice):
			# Legacy fallback for acquisitions posted before the multi-seller PI flow.
			seller_posted_cost = flt(
				frappe.db.sql(
					"""
					select ifnull(sum(debit - credit), 0)
					from `tabGL Entry`
					where voucher_type = 'Purchase Invoice'
					  and voucher_no = %s
					  and land_acquisition = %s
					  and account = %s
					  and ifnull(is_cancelled, 0) = 0
					""",
					(doc.purchase_invoice, doc.name, land_account),
				)[0][0]
				or 0
			)

		supplier_invoice_rows = frappe.db.sql(
			"""
			select
				coalesce(nullif(pi.supplier_name, ''), pi.supplier) as supplier_name,
				pii.purchase_orders,
				gl.voucher_no as purchase_invoice,
				sum(gl.debit - gl.credit) as amount_tzs,
				'Purchase Invoice' as source_doctype,
				'Posted' as status_label,
				pi.posting_date as posting_date
			from `tabGL Entry` gl
			inner join `tabPurchase Invoice` pi on pi.name = gl.voucher_no
			inner join `tabSupplier` supplier on supplier.name = pi.supplier
			left join (
				select
					parent,
					group_concat(distinct purchase_order order by purchase_order separator ', ') as purchase_orders
				from `tabPurchase Invoice Item`
				where land_acquisition = %s
				group by parent
			) pii on pii.parent = pi.name
			where gl.voucher_type = 'Purchase Invoice'
			  and gl.land_acquisition = %s
			  and gl.account = %s
			  and ifnull(gl.is_cancelled, 0) = 0
			  and pi.docstatus = 1
			  and ifnull(supplier.is_land_seller, 0) = 0
			group by gl.voucher_no, pi.supplier, pi.supplier_name, pi.posting_date, pi.creation
			having abs(sum(gl.debit - gl.credit)) > 0.0001
			order by pi.posting_date desc, pi.creation desc
			""",
			(doc.name, doc.name, land_account),
			as_dict=True,
		)
		for row in supplier_invoice_rows:
			row.amount_tzs = flt(row.amount_tzs)
		supplier_pi_rows = supplier_invoice_rows
		supplier_invoice_total = sum(flt(row.amount_tzs) for row in supplier_invoice_rows)

	land_seller_rows = _build_land_seller_summary_rows(seller_po_rows, seller_pi_rows)
	other_supplier_rows = _build_other_supplier_summary_rows(supplier_po_rows, supplier_pi_rows)

	if frappe.db.has_column("Purchase Order Item", "land_acquisition"):
		committed_cost = flt(
			frappe.db.sql(
				"""
				select ifnull(sum(greatest(poi.base_net_amount - ifnull(poi.billed_amt * po.conversion_rate, 0), 0)), 0)
				from `tabPurchase Order Item` poi
				inner join `tabPurchase Order` po on po.name = poi.parent
				left join `tabSupplier` supplier on supplier.name = po.supplier
				where po.docstatus = 1
				  and po.status not in ('Closed', 'Completed', 'Cancelled')
				  and poi.land_acquisition = %s
				  and ifnull(supplier.is_land_seller, 0) = 0
				""",
				(doc.name,),
			)[0][0]
			or 0
		)

	# Total land cost now comes only from Purchase Invoices tagged to this
	# Land Acquisition. The seller/non-seller split remains useful for the
	# procurement summaries, but it no longer creates a separate visible base cost.
	seller_purchase_amount_tzs = 0.0
	additional_project_cost = max(0.0, flt(actual_posted_cost) - flt(seller_posted_cost))
	acquisition_cost = flt(actual_posted_cost)
	cost_per_sqm_tzs = 0.0
	if flt(doc.total_area_sqm) > 0:
		cost_per_sqm_tzs = flt(acquisition_cost) / flt(doc.total_area_sqm)

	return {
		"acquisition_currency": doc.get("acquisition_currency") or "TZS",
		"exchange_rate": 1.0,
		"seller_purchase_amount_tzs": seller_purchase_amount_tzs,
		"committed_cost_tzs": committed_cost,
		"additional_project_cost_tzs": additional_project_cost,
		"acquisition_cost_tzs": acquisition_cost,
		"cost_per_sqm_tzs": cost_per_sqm_tzs,
		"total_acquisition_cost": acquisition_cost,
		"land_seller_rows": land_seller_rows,
		"other_supplier_rows": other_supplier_rows,
		"supplier_invoice_rows": supplier_invoice_rows,
		"supplier_invoice_total_tzs": supplier_invoice_total,
	}


def sync_land_acquisition_costs(land_acquisitions):
	land_acquisition_names = {name for name in (land_acquisitions or []) if name}
	if not land_acquisition_names:
		return

	for land_acquisition in land_acquisition_names:
		sync_land_acquisition_cost_summary(land_acquisition)


def sync_costs_from_purchase_order(doc, method=None):
	land_acquisitions = {doc.get("land_acquisition")}
	land_acquisitions.update({row.land_acquisition for row in (doc.get("items") or []) if row.land_acquisition})
	sync_land_acquisition_costs(land_acquisitions)


def sync_costs_from_purchase_invoice(doc, method=None):
	land_acquisitions = {doc.get("land_acquisition")}
	land_acquisitions.update({row.land_acquisition for row in (doc.get("items") or []) if row.land_acquisition})
	sync_land_acquisition_costs(land_acquisitions)


def sync_costs_from_journal_entry(doc, method=None):
	sync_land_acquisition_costs({row.land_acquisition for row in (doc.get("accounts") or []) if row.land_acquisition})


def _build_land_seller_summary_rows(seller_po_rows, seller_pi_rows):
	rows = []
	linked_pi_by_po = {}
	pis_linked_to_po = set()

	for pi_row in seller_pi_rows:
		purchase_orders = [value.strip() for value in (pi_row.get("purchase_orders") or "").split(",") if value.strip()]
		for purchase_order in purchase_orders:
			linked_pi_by_po.setdefault(purchase_order, []).append(pi_row)
			pis_linked_to_po.add(pi_row.purchase_invoice)

	for po_row in seller_po_rows:
		rows.append({
			"supplier_name": po_row.supplier_name,
			"title_deed_number": po_row.get("title_deed_number") or "",
			"seller_id_type": po_row.get("seller_id_type") or "",
			"seller_id_number": po_row.get("seller_id_number") or "",
			"source_doctype": "Purchase Order",
			"purchase_order": po_row.purchase_order,
			"purchase_invoice": ", ".join(pi.purchase_invoice for pi in linked_pi_by_po.get(po_row.purchase_order, [])),
			"amount_tzs": flt(po_row.amount_tzs),
			"status_label": po_row.status_label,
			"posting_date": po_row.posting_date,
		})

	for pi_row in seller_pi_rows:
		if pi_row.purchase_invoice not in pis_linked_to_po:
			rows.append({
				"supplier_name": pi_row.supplier_name,
				"title_deed_number": pi_row.get("title_deed_number") or "",
				"seller_id_type": pi_row.get("seller_id_type") or "",
				"seller_id_number": pi_row.get("seller_id_number") or "",
				"source_doctype": "Purchase Invoice",
				"purchase_order": pi_row.get("purchase_orders") or "",
				"purchase_invoice": pi_row.purchase_invoice,
				"amount_tzs": flt(pi_row.amount_tzs),
				"status_label": pi_row.status_label,
				"posting_date": pi_row.posting_date,
			})

	rows.sort(key=lambda row: ((row.get("posting_date") or ""), row.get("purchase_invoice") or row.get("purchase_order") or ""), reverse=True)
	return rows


def _build_other_supplier_summary_rows(supplier_po_rows, supplier_pi_rows):
	rows = []
	linked_pi_by_po = {}
	pis_linked_to_po = set()

	for pi_row in supplier_pi_rows:
		purchase_orders = [value.strip() for value in (pi_row.get("purchase_orders") or "").split(",") if value.strip()]
		for purchase_order in purchase_orders:
			linked_pi_by_po.setdefault(purchase_order, []).append(pi_row)
			pis_linked_to_po.add(pi_row.purchase_invoice)

	for po_row in supplier_po_rows:
		rows.append({
			"supplier_name": po_row.supplier_name,
			"source_doctype": "Purchase Order",
			"purchase_order": po_row.purchase_order,
			"purchase_invoice": ", ".join(pi.purchase_invoice for pi in linked_pi_by_po.get(po_row.purchase_order, [])),
			"amount_tzs": flt(po_row.amount_tzs),
			"status_label": po_row.status_label,
			"posting_date": po_row.posting_date,
		})

	for pi_row in supplier_pi_rows:
		if pi_row.purchase_invoice not in pis_linked_to_po:
			rows.append({
				"supplier_name": pi_row.supplier_name,
				"source_doctype": "Purchase Invoice",
				"purchase_order": pi_row.get("purchase_orders") or "",
				"purchase_invoice": pi_row.purchase_invoice,
				"amount_tzs": flt(pi_row.amount_tzs),
				"status_label": pi_row.status_label,
				"posting_date": pi_row.posting_date,
			})

	rows.sort(key=lambda row: ((row.get("posting_date") or ""), row.get("purchase_invoice") or row.get("purchase_order") or ""), reverse=True)
	return rows
