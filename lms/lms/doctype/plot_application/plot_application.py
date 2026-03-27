import frappe
from frappe.model.document import Document
from frappe.utils import flt, add_days, today, getdate, cint

from lms.lms.doctype.land_acquisition.land_acquisition import sync_land_acquisition_plot_summary


class PlotApplication(Document):

	# ------------------------------------------------------------------ #
	#  Validate                                                            #
	# ------------------------------------------------------------------ #

	def validate(self):
		self.validate_plot_available()
		self.fill_plot_details()
		self.fill_fee_from_settings()

	def before_submit(self):
		"""Final gate before submit: only one active submitted/paid app per plot."""
		self._lock_plot_row()
		self._ensure_no_other_active_application_for_submit()

	def before_cancel(self):
		if self.status == "Converted":
			frappe.throw(
				"This application has already been converted into an active sale. "
				"Cancel the Sales Order before first payment, or terminate the Plot Contract after payment."
			)

	def validate_plot_available(self):
		"""Draft-time checks for plot availability and existing active applications."""
		if not self.plot:
			return
		# Draft creation/update checks only.
		if self.docstatus == 0:
			plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
			if plot_status != "Available":
				frappe.throw(
					f"Plot {self.plot} is not Available (current status: {plot_status}). "
					"Only Available plots can be applied for."
				)
			active = self._get_other_active_application(("Submitted", "Paid", "Converted"))
			if active:
				frappe.throw(
					f"Plot {self.plot} already has an active application ({active.name}, status: {active.status}). "
					"That application must expire or be cancelled first."
				)

	def _lock_plot_row(self):
		"""Serialize submit/payment operations per plot to reduce race conditions."""
		if self.plot:
			frappe.db.sql(
				"select name from `tabPlot Master` where name=%s for update",
				(self.plot,),
			)

	def _get_other_active_application(self, statuses):
		return frappe.db.get_value(
			"Plot Application",
			{
				"plot": self.plot,
				"docstatus": 1,
				"status": ["in", list(statuses)],
				"name": ("!=", self.name),
			},
			["name", "status"],
			as_dict=True,
		)

	def _ensure_no_other_active_application_for_submit(self):
		if not self.plot:
			return

		plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
		if plot_status != "Available":
			frappe.throw(
				f"Cannot submit application for plot {self.plot}. "
				f"Plot status is {plot_status}, expected Available."
			)

		active = self._get_other_active_application(("Submitted", "Paid", "Converted"))
		if active:
			frappe.throw(
				f"Cannot submit {self.name}. Plot {self.plot} already has active application "
				f"{active.name} (status: {active.status})."
			)

	def fill_plot_details(self):
		"""Auto-fill acquisition info from the selected plot."""
		if self.plot:
			plot_data = frappe.db.get_value(
				"Plot Master", self.plot,
				["land_acquisition"],
				as_dict=True,
			)
			if plot_data:
				self.land_acquisition = plot_data.land_acquisition
				self.acquisition_name = frappe.db.get_value(
					"Land Acquisition", plot_data.land_acquisition, "acquisition_name"
				) or ""

	def fill_fee_from_settings(self):
		"""Pull application fee amount and validity days from LMS Settings."""
		settings = frappe.get_single("LMS Settings")
		self.application_fee = flt(settings.application_fee_amount)
		self.unpaid_validity_days = int(settings.unpaid_application_expiry_days or 3)
		self.validity_days = int(settings.application_fee_validity_days or 7)
		if not self.application_fee:
			frappe.throw("Application Fee Amount is not configured in LMS Settings.")

	# ------------------------------------------------------------------ #
	#  Submit / Cancel                                                     #
	# ------------------------------------------------------------------ #

	def on_submit(self):
		self.db_set("status", "Submitted")
		if self.plot:
			current_plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
			if current_plot_status == "Available":
				frappe.db.set_value("Plot Master", self.plot, "status", "Pending Fee")
				self._sync_land_acquisition_summary()

	def on_cancel(self):
		"""Handle cancellation — manual, auto-cancel (unpaid), or auto-expire (paid past deadline).

        The scheduler sets doc.flags._cancellation_reason before calling cancel():
          - "Expired"   → paid application past its reservation deadline
          - (default)   → unpaid timeout or manual cancel by user
        """
		# If plot was locked by this application, release it.
		if self.status in ("Submitted", "Paid"):
			plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
			if plot_status in ("Pending Fee", "Pending Advance", "Reserved"):
				frappe.db.set_value("Plot Master", self.plot, "status", "Available")
				self._sync_land_acquisition_summary()

		if self.status == "Paid":
			self._cancel_linked_sales_order_if_safe()
		
		reason = getattr(self.flags, "_cancellation_reason", None)
		if reason == "Expired":
			self.db_set("status", "Expired")
		else:
			self.db_set("status", "Cancelled")

	def _sync_land_acquisition_summary(self):
		land_acquisition = frappe.db.get_value("Plot Master", self.plot, "land_acquisition")
		if land_acquisition:
			sync_land_acquisition_plot_summary(land_acquisition)

	def _cancel_linked_sales_order_if_safe(self):
		if not self.sales_order or not frappe.db.exists("Sales Order", self.sales_order):
			return

		so = frappe.get_doc("Sales Order", self.sales_order)
		if so.docstatus == 0:
			frappe.delete_doc("Sales Order", so.name, ignore_permissions=True)
			self.db_set("sales_order", "")
			return

		plot_invoice = so.get("plot_sales_invoice")
		if plot_invoice and frappe.db.exists("Sales Invoice", plot_invoice):
			outstanding, grand_total = frappe.db.get_value(
				"Sales Invoice",
				plot_invoice,
				["outstanding_amount", "grand_total"],
			)
			if flt(outstanding) < flt(grand_total):
				return

		so.cancel()
		self.db_set("sales_order", "")

	# ------------------------------------------------------------------ #
	#  Record Application Fee Payment                                      #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def record_fee_payment(self, payment_date, bank_account=None, reference_no=None):
		"""Record the application fee via a Sales Invoice.

		Accounting:
		  SI submit → Dr Accounts Receivable / Cr Application Fee Income
		  PE submit → Dr Bank/Cash / Cr Accounts Receivable

		After recording:
		  - Plot status → Pending Advance
		  - Expiry date calculated (payment_date + validity_days)
		  - Application status → Paid
		  - Application fee SI is fully settled (Paid)
		  - Sales Order can be created manually later from this application
		  - No other application can take the plot while advance is pending
		"""
		if self.status != "Submitted":
			frappe.throw("Application fee can only be recorded on a Submitted application.")

		if self.sales_invoice:
			frappe.throw("Application fee has already been recorded.")

		# Re-check under lock so only one submitted app can proceed to paid.
		self._lock_plot_row()
		active_paid = self._get_other_active_application(("Paid", "Converted"))
		if active_paid:
			frappe.throw(
				f"Cannot record payment for {self.name}. Plot {self.plot} is already reserved by "
				f"application {active_paid.name} (status: {active_paid.status})."
			)

		plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
		if plot_status not in ("Available", "Pending Fee"):
			frappe.throw(
				f"Cannot record payment for {self.name}. Plot {self.plot} is {plot_status}, "
				"not ready for application fee payment."
			)

		settings = frappe.get_single("LMS Settings")
		bank_account = bank_account or settings.application_fee_receiving_account
		if not bank_account:
			frappe.throw(
				"Application Fee Receiving Account is not configured in LMS Settings. "
				"Set it first, or select a Bank/Cash account while recording payment."
			)
		fee_amount = flt(self.application_fee)
		self._validate_receiving_account(bank_account, settings.company)

		if fee_amount <= 0:
			frappe.throw("Application fee amount must be greater than zero.")

		# Create and submit Sales Invoice
		si = frappe.get_doc({
			"doctype": "Sales Invoice",
			"customer": self.customer,
			"posting_date": payment_date,
			"due_date": payment_date,
			"company": settings.company,
			"remarks": (
				f"Application fee for Plot {self.plot} — "
				f"Application {self.name}"
			),
			"items": [{
				"item_name": "Application Fee",
				"description": f"Plot application fee — {self.name} / Plot {self.plot}",
				"qty": 1,
				"rate": fee_amount,
				"income_account": settings.application_fee_income_account,
			}],
		})
		si.insert(ignore_permissions=True)
		si.submit()

		# Create and submit Payment Entry to settle the SI immediately.
		pe = frappe.get_doc({
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"posting_date": payment_date,
			"company": settings.company,
			"party_type": "Customer",
			"party": self.customer,
			"paid_from": si.debit_to,
			"paid_to": bank_account,
			"paid_amount": fee_amount,
			"received_amount": fee_amount,
			"reference_no": reference_no or self.name,
			"reference_date": payment_date,
			"remarks": f"Plot Application Fee Payment — {self.name} / Plot {self.plot}",
			"references": [{
				"reference_doctype": "Sales Invoice",
				"reference_name": si.name,
				"allocated_amount": fee_amount,
			}],
		})
		pe.insert(ignore_permissions=True)
		pe.submit()

		# Calculate expiry date
		validity = int(self.validity_days or 7)
		expiry = add_days(payment_date, validity)

		# Update application
		self.db_set("sales_invoice", si.name)
		self.db_set("payment_entry", pe.name)
		self.db_set("payment_date", payment_date)
		self.db_set("reference_no", reference_no or "")
		self.db_set("expiry_date", expiry)
		self.db_set("status", "Paid")

		# Lock the plot until first advance payment or expiry
		frappe.db.set_value("Plot Master", self.plot, "status", "Pending Advance")
		self._sync_land_acquisition_summary()

		frappe.msgprint(
			f"Application fee of TZS {fee_amount:,.0f} recorded. "
			f"Plot {self.plot} is now in Pending Advance until {expiry}. "
			f"Sales Invoice {si.name} fully paid via Payment Entry {pe.name}. "
			"Next: create the Sales Order and collect the first advance within the validity window.",
			indicator="green",
			alert=True,
		)

		return si.name

	def _validate_receiving_account(self, account, company):
		account_info = frappe.db.get_value(
			"Account",
			account,
			["name", "company", "account_type", "is_group"],
			as_dict=True,
		)
		if not account_info:
			frappe.throw(f"Receiving account {account} was not found.")
		if cint(account_info.is_group):
			frappe.throw(f"{account} is a group account. Please choose a posting account.")
		if account_info.account_type not in ("Bank", "Cash"):
			frappe.throw(f"{account} is not a Bank/Cash account.")
		if account_info.company and account_info.company != company:
			frappe.throw(
				f"Receiving account {account} belongs to company {account_info.company}, "
				f"not {company}."
			)

	# ------------------------------------------------------------------ #
	#  Create ERP Sales Order                                              #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def create_sales_order(self, notify=1):
		"""Create a draft ERP Sales Order from this paid application."""
		notify = cint(notify)

		if self.status != "Paid":
			frappe.throw("A Sales Order can only be created from a Paid application.")

		if self.sales_order and frappe.db.exists("Sales Order", self.sales_order):
			frappe.throw(
				f"A Sales Order has already been created: {self.sales_order}"
			)

		# Clean stale link if it points to a missing SO.
		if self.sales_order and not frappe.db.exists("Sales Order", self.sales_order):
			self.db_set("sales_order", "")

		# Check expiry
		if self.expiry_date and getdate(self.expiry_date) < getdate(today()):
			frappe.throw(
				"This application has expired. The plot reservation is no longer valid."
			)

		settings = frappe.get_single("LMS Settings")
		plot = frappe.get_doc("Plot Master", self.plot)
		payment_completion_days = cint(plot.payment_completion_days or 0)
		if payment_completion_days <= 0:
			frappe.throw(f"Plot {plot.name} is missing Payment Completion Days.")

		from lms.sales_order_hooks import _build_sales_order_item_row

		transaction_date = self.payment_date or today()
		payment_deadline = add_days(transaction_date, payment_completion_days)
		item_row = _build_sales_order_item_row(plot, settings.plot_inventory_warehouse, payment_deadline)

		so = frappe.get_doc({
			"doctype": "Sales Order",
			"company": settings.company,
			"customer": self.customer,
			"transaction_date": transaction_date,
			"delivery_date": payment_deadline,
			"set_warehouse": settings.plot_inventory_warehouse,
			"plot": plot.name,
			"land_acquisition": plot.land_acquisition,
			"acquisition_name": plot.acquisition_name,
			"plot_application": self.name,
			"booking_fee_percent": flt(plot.booking_fee_percent),
			"government_share_percent": flt(plot.government_share_percent),
			"payment_completion_days": payment_completion_days,
			"payment_deadline": payment_deadline,
			"items": [item_row],
		})
		so.insert(ignore_permissions=True)

		self.db_set("sales_order", so.name)

		if notify:
			frappe.msgprint(
				f"Sales Order <b>{so.name}</b> created. "
				"You can now review and submit it.",
				indicator="green",
				alert=True,
			)

		return so.name
