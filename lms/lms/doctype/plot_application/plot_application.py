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

	def on_cancel(self):
		"""Handle cancellation — manual, auto-cancel (unpaid), or auto-expire (paid past deadline).

        The scheduler sets doc.flags._cancellation_reason before calling cancel():
          - "Expired"   → paid application past its reservation deadline
          - (default)   → unpaid timeout or manual cancel by user
        """
		# If plot was reserved by this application, release it
		if self.status == "Paid":
			plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
			if plot_status == "Reserved":
				frappe.db.set_value("Plot Master", self.plot, "status", "Available")
				self._sync_land_acquisition_summary()
		
		reason = getattr(self.flags, "_cancellation_reason", None)
		if reason == "Expired":
			self.db_set("status", "Expired")
		else:
			self.db_set("status", "Cancelled")

	def _sync_land_acquisition_summary(self):
		land_acquisition = frappe.db.get_value("Plot Master", self.plot, "land_acquisition")
		if land_acquisition:
			sync_land_acquisition_plot_summary(land_acquisition)

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
		  - Plot status → Reserved
		  - Expiry date calculated (payment_date + validity_days)
		  - Application status → Paid
		  - Application fee SI is fully settled (Paid)
		  - Sales Order can be created manually later from this application
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
		if plot_status != "Available":
			frappe.throw(
				f"Cannot record payment for {self.name}. Plot {self.plot} is {plot_status}, "
				"not Available."
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

		# Reserve the plot
		frappe.db.set_value("Plot Master", self.plot, "status", "Reserved")
		self._sync_land_acquisition_summary()

		frappe.msgprint(
			f"Application fee of TZS {fee_amount:,.0f} recorded. "
			f"Plot {self.plot} reserved until {expiry}. "
			f"Sales Invoice {si.name} fully paid via Payment Entry {pe.name}. "
			"Next: create the Sales Order when terms are ready.",
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
	#  Create Plot Sales Order                                             #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def create_sales_order(self, booking_fee_percent=None, government_share_percent=None, payment_completion_days=None, notify=1):
		"""Create a Plot Sales Order from this paid application.

		This keeps the quick path from Plot Application while manual SO creation
		from Plot Sales Order list also remains available.
		"""
		notify = cint(notify)
		booking_fee_percent = flt(booking_fee_percent)
		government_share_percent = flt(government_share_percent)
		payment_completion_days = cint(payment_completion_days) or 90

		if self.status != "Paid":
			frappe.throw("A Sales Order can only be created from a Paid application.")

		if self.plot_sales_order and frappe.db.exists("Plot Sales Order", self.plot_sales_order):
			frappe.throw(
				f"A Sales Order has already been created: {self.plot_sales_order}"
			)

		# Clean stale link if it points to a missing SO.
		if self.plot_sales_order and not frappe.db.exists("Plot Sales Order", self.plot_sales_order):
			self.db_set("plot_sales_order", "")

		# Check expiry
		if self.expiry_date and getdate(self.expiry_date) < getdate(today()):
			frappe.throw(
				"This application has expired. The plot reservation is no longer valid."
			)

		if booking_fee_percent <= 0 or booking_fee_percent > 100:
			frappe.throw("Booking Fee % is required and must be between 0 and 100.")
		if government_share_percent < 0 or government_share_percent > 100:
			frappe.throw("Government Share % is required and must be between 0 and 100.")
		if payment_completion_days <= 0:
			frappe.throw("Payment Completion Days must be greater than zero.")

		so = frappe.get_doc({
			"doctype": "Plot Sales Order",
			"customer": self.customer,
			"plot": self.plot,
			"order_date": self.payment_date or today(),
			"plot_application": self.name,
			"booking_fee_percent": booking_fee_percent,
			"government_share_percent": government_share_percent,
			"payment_completion_days": payment_completion_days,
		})
		so.insert(ignore_permissions=True)

		self.db_set("plot_sales_order", so.name)
		self.db_set("status", "Converted")

		if notify:
			frappe.msgprint(
				f"Plot Sales Order <b>{so.name}</b> created. "
				"You can now review and submit it.",
				indicator="green",
				alert=True,
			)

		return so.name
