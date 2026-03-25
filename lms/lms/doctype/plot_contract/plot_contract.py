import frappe
from frappe.model.document import Document
from frappe.utils import flt, add_days, today, getdate, cint

from lms.lms.doctype.land_acquisition.land_acquisition import sync_land_acquisition_plot_summary
from lms.lms.doctype.plot_master.plot_master import PLOT_TYPE_TO_ITEM


class PlotContract(Document):

	# ------------------------------------------------------------------ #
	#  Validate                                                            #
	# ------------------------------------------------------------------ #

	def validate(self):
		self.validate_plot_available()
		self.fill_selling_price()
		self.calculate_financials()
		self.generate_payment_schedule()
		self.calculate_payment_summary()

	def before_submit(self):
		self._validate_sales_order_first_payment_gate()

	def before_cancel(self):
		# Cancel is only allowed for contracts with no confirmed payment.
		# If any payment exists, use terminate_contract() so forfeiture accounting is posted.
		if flt(self.total_paid) > 0:
			frappe.throw(
				f"Contract {self.name} has received payments (TZS {flt(self.total_paid):,.0f}). "
				"Use Terminate Contract instead of Cancel."
			)

	def validate_plot_available(self):
		if not self.plot:
			return
		# Skip check when contract is auto-created from a Plot Sales Order —
		# the SO already validated and reserved the plot.
		if self.sales_order or self.flags.get("from_sales_order"):
			return
		if not frappe.db.exists("Plot Contract", self.name):
			plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
			if plot_status != "Available":
				frappe.throw(
					f"Plot {self.plot} is not Available (current status: {plot_status}). "
					"Only Available plots can be contracted."
				)
			active = frappe.db.exists("Plot Contract", {
				"plot": self.plot,
				"docstatus": 1,
				"contract_status": ["in", ["Ongoing", "Completed"]],
			})
			if active:
					frappe.throw(
						f"Plot {self.plot} already has an active contract ({active}). "
						"The existing contract must be terminated or completed first."
					)

	def _sales_order_has_any_confirmed_payment(self, so_doc):
		"""True when any linked SO installment SI has been partially/fully paid."""
		if so_doc.doctype == "Sales Order":
			plot_invoice = so_doc.get("plot_sales_invoice")
			if not plot_invoice or not frappe.db.exists("Sales Invoice", plot_invoice):
				return False
			si = frappe.db.get_value(
				"Sales Invoice",
				plot_invoice,
				["docstatus", "outstanding_amount", "grand_total"],
				as_dict=True,
			)
			return bool(si and si.docstatus == 1 and flt(si.outstanding_amount) < flt(si.grand_total))

		if flt(so_doc.total_paid) > 0:
			return True

		so_rows = frappe.db.get_all(
			"Plot Contract Payment",
			filters={
				"parenttype": "Plot Sales Order",
				"parent": so_doc.name,
				"sales_invoice": ["!=", ""],
			},
			fields=["sales_invoice", "expected_amount"],
		)
		for row in so_rows:
			si = frappe.db.get_value(
				"Sales Invoice",
				row.sales_invoice,
				["docstatus", "outstanding_amount"],
				as_dict=True,
			)
			if not si or si.docstatus != 1:
				continue
			if flt(si.outstanding_amount) < flt(row.expected_amount):
				return True
		return False

	def _validate_sales_order_first_payment_gate(self):
		"""Contracts linked to SO can only be activated after first SO payment."""
		if not self.sales_order:
			return

		if frappe.db.exists("Sales Order", self.sales_order):
			so_doc = frappe.get_doc("Sales Order", self.sales_order)
		elif frappe.db.exists("Plot Sales Order", self.sales_order):
			so_doc = frappe.get_doc("Plot Sales Order", self.sales_order)
		else:
			frappe.throw(f"Linked Sales Order {self.sales_order} was not found.")

		if so_doc.docstatus != 1:
			frappe.throw(
				f"Linked Sales Order {so_doc.name} is not submitted. "
				"Submit the Sales Order first."
			)

		if so_doc.plot != self.plot or so_doc.customer != self.customer:
			frappe.throw(
				f"Contract {self.name} does not match linked Sales Order {so_doc.name} "
				"(plot/customer mismatch)."
			)

		if not self._sales_order_has_any_confirmed_payment(so_doc):
			frappe.throw(
				f"Cannot submit contract {self.name} before first payment on Sales Order "
				f"{so_doc.name}. Record payment on the Sales Order first."
			)

	def _get_standard_sales_order_doc(self):
		if not self.sales_order or not frappe.db.exists("Sales Order", self.sales_order):
			return None
		return frappe.get_doc("Sales Order", self.sales_order)

	def _get_standard_sales_order_invoice_name(self):
		so_doc = self._get_standard_sales_order_doc()
		if not so_doc:
			return ""
		invoice_name = so_doc.get("plot_sales_invoice") or ""
		if invoice_name and frappe.db.exists("Sales Invoice", invoice_name):
			return invoice_name
		return ""

	def fill_selling_price(self):
		if self.plot:
			plot_data = frappe.db.get_value(
				"Plot Master", self.plot,
				["selling_price", "land_acquisition"],
				as_dict=True,
			)
			if plot_data:
				if not flt(self.selling_price):
					self.selling_price = plot_data.selling_price
				self.land_acquisition = plot_data.land_acquisition
				self.acquisition_name = frappe.db.get_value(
					"Land Acquisition", plot_data.land_acquisition, "acquisition_name"
				) or ""

	def calculate_financials(self):
		if flt(self.selling_price) > 0 and flt(self.booking_fee_percent) > 0:
			self.booking_fee_amount = flt(self.selling_price) * flt(self.booking_fee_percent) / 100
			self.balance_due = flt(self.selling_price) - self.booking_fee_amount
		if self.contract_date and flt(self.payment_completion_days) > 0:
			self.payment_deadline = add_days(self.contract_date, int(self.payment_completion_days))

	def generate_payment_schedule(self):
		"""Rebuild schedule rows from contract parameters.
		Only runs while the document is still in Draft — once submitted,
		rows are managed by sync_payment_status() after each payment.

		Skipped when contract is auto-created from a Plot Sales Order —
		the schedule (with SI links) is copied directly from the SO.

		Schedule:
		  Row 1 — Booking fee, due on contract date
		  Row 2 — Remaining balance, due on contract date + payment_completion_days
		"""
		if self.docstatus == 1:
			return
		# Skip regeneration if this contract comes from a Sales Order —
		# the payment schedule with SI links is already set.
		if self.sales_order or self.flags.get("from_sales_order"):
			return
		if not flt(self.selling_price) or not flt(self.booking_fee_percent):
			return
		if not self.contract_date:
			return

		booking_fee = flt(self.booking_fee_amount) or (
			flt(self.selling_price) * flt(self.booking_fee_percent) / 100
		)
		balance = flt(self.selling_price) - booking_fee
		total_days = int(self.payment_completion_days or 90)

		self.payment_schedule = []

		# Row 1 — booking fee, due on contract date
		self.append("payment_schedule", {
			"installment_number": 1,
			"due_date": self.contract_date,
			"expected_amount": booking_fee,
			"paid_amount": 0,
			"status": "Pending",
		})

		# Row 2 — remaining balance, due on contract date + total days
		if balance > 0:
			self.append("payment_schedule", {
				"installment_number": 2,
				"due_date": add_days(self.contract_date, total_days),
				"expected_amount": balance,
				"paid_amount": 0,
				"status": "Pending",
			})

	def calculate_payment_summary(self):
		self.total_contract_value = flt(self.selling_price)
		total_paid = sum(flt(row.paid_amount) for row in self.payment_schedule)
		self.total_paid = total_paid
		total_outstanding = flt(self.selling_price) - total_paid
		self.total_outstanding = total_outstanding
		self.payment_progress = self._derive_payment_progress(total_paid, total_outstanding)
		if flt(self.government_share_percent) > 0:
			self.government_fee_withheld = (
				flt(self.selling_price) * flt(self.government_share_percent) / 100
			)

	def _derive_payment_progress(self, total_paid, total_outstanding):
		if flt(total_paid) <= 0:
			return "Unpaid"
		if flt(total_outstanding) <= 0:
			return "Fully Paid"

		first_expected = 0.0
		first_paid = 0.0
		for row in self.payment_schedule:
			if cint(row.installment_number or 0) == 1:
				first_expected = flt(row.expected_amount)
				first_paid = flt(row.paid_amount)
				break

		if first_expected > 0 and first_paid >= first_expected:
			later_paid = sum(
				flt(row.paid_amount)
				for row in self.payment_schedule
				if cint(row.installment_number or 0) > 1
			)
			if later_paid > 0:
				return "Advance + Installments Paid"
			return "Advance Paid"

		return "Partially Paid"

	# ------------------------------------------------------------------ #
	#  Submit / Cancel                                                     #
	# ------------------------------------------------------------------ #

	def on_submit(self):
		frappe.db.set_value("Plot Master", self.plot, "status", "Reserved")
		self._sync_land_acquisition_summary()
		self.db_set("contract_status", "Ongoing")
		# Keep SI creation tied to confirmed payments (record_payment).

	def on_cancel(self):
		frappe.db.set_value("Plot Master", self.plot, "status", "Available")
		self._sync_land_acquisition_summary()
		self.db_set("contract_status", "Cancelled")
		self._cancel_sales_invoices()

	def _sync_land_acquisition_summary(self):
		land_acquisition = self.land_acquisition or frappe.db.get_value(
			"Plot Master", self.plot, "land_acquisition"
		)
		if land_acquisition:
			sync_land_acquisition_plot_summary(land_acquisition)

	# ------------------------------------------------------------------ #
	#  Sales Invoice helpers                                               #
	# ------------------------------------------------------------------ #

	def _ensure_row_sales_invoice(self, row, settings):
		"""Create and link SI for a payment row if missing; return SI name."""
		if row.sales_invoice:
			return row.sales_invoice

		plot_type = frappe.db.get_value("Plot Master", self.plot, "plot_type")
		item_code = PLOT_TYPE_TO_ITEM.get(plot_type)
		if not item_code:
			frappe.throw(f"No item mapped for plot type '{plot_type}'.")

		description = (
			f"Booking Fee — Plot {self.plot} ({self.name})"
			if cint(row.installment_number or 0) == 1
			else f"Installment {row.installment_number} — Plot {self.plot} ({self.name})"
		)

		si_name = self._make_sales_invoice(
			item_code=item_code,
			amount=flt(row.expected_amount),
			due_date=row.due_date,
			description=description,
			settings=settings,
			submit=True,
		)
		frappe.db.set_value("Plot Contract Payment", row.name, "sales_invoice", si_name)
		row.sales_invoice = si_name

		if cint(row.installment_number or 0) == 1:
			self.db_set("booking_fee_invoice", si_name)

		frappe.logger("lms").info(
			f"Created SI {si_name} for contract {self.name} "
			f"(installment #{row.installment_number}) after payment confirmation"
		)
		return si_name

	def _make_sales_invoice(self, item_code, amount, due_date, description, settings, submit=True):
		si = frappe.get_doc({
			"doctype": "Sales Invoice",
			"customer": self.customer,
			"posting_date": today(),
			"due_date": due_date,
			"company": settings.company,
			"remarks": description,
			"items": [{
				"item_code": item_code,
				"qty": 1,
				"rate": amount,
				"income_account": settings.customer_advance_account,
				"description": description,
			}],
		})
		si.insert(ignore_permissions=True)
		if submit:
			si.submit()
		return si.name

	def _cancel_sales_invoices(self):
		for row in self.payment_schedule:
			if not row.sales_invoice:
				continue
			si_name = row.sales_invoice
			frappe.db.set_value("Plot Contract Payment", row.name, "sales_invoice", "")
			si_doc = frappe.get_doc("Sales Invoice", si_name)
			if si_doc.docstatus == 1:
				si_doc.cancel()
			elif si_doc.docstatus == 0:
				frappe.delete_doc("Sales Invoice", si_name, ignore_permissions=True)

	def _cancel_unpaid_invoices(self):
		"""Cancel submitted SIs and delete Draft SIs for all unpaid installments.

		Called during contract termination to clean up outstanding invoices.
		Paid rows are left untouched — those SIs are already settled.
		"""
		for row in self.payment_schedule:
			if not row.sales_invoice:
				continue
			if row.status == "Paid":
				continue  # settled — leave as-is
			si_name = row.sales_invoice
			# Clear the link first so Frappe's link validator does not block cancellation
			frappe.db.set_value("Plot Contract Payment", row.name, "sales_invoice", "")
			si_doc = frappe.get_doc("Sales Invoice", si_name)
			if si_doc.docstatus == 0:
				frappe.delete_doc("Sales Invoice", si_name, ignore_permissions=True)
			elif si_doc.docstatus == 1:
				if flt(si_doc.outstanding_amount) > 0:
					si_doc.cancel()

	def _validate_bank_account(self, bank_account, company):
		account_info = frappe.db.get_value(
			"Account",
			bank_account,
			["name", "company", "account_type", "is_group"],
			as_dict=True,
		)
		if not account_info:
			frappe.throw(f"Bank account {bank_account} was not found.")
		if cint(account_info.is_group):
			frappe.throw(f"{bank_account} is a group account. Please choose a posting bank account.")
		if account_info.account_type != "Bank":
			frappe.throw(f"{bank_account} is not a Bank account.")
		if account_info.company and account_info.company != company:
			frappe.throw(
				f"Bank account {bank_account} belongs to company {account_info.company}, "
				f"not {company}."
			)

	def _check_duplicate_reference(self, reference_no):
		if not reference_no:
			return
		existing = frappe.db.sql(
			"""
			select pe.name
			from `tabPayment Entry` pe
			inner join `tabPayment Entry Reference` per
				on per.parent = pe.name
			inner join `tabPlot Contract Payment` pcp
				on pcp.sales_invoice = per.reference_name
			where pe.docstatus = 1
			  and pe.reference_no = %s
			  and pcp.parenttype = 'Plot Contract'
			  and pcp.parent = %s
			limit 1
			""",
			(reference_no, self.name),
			as_dict=True,
		)
		if existing:
			frappe.throw(
				f"Duplicate payment reference '{reference_no}'. "
				f"Payment Entry {existing[0].name} already exists for this contract."
			)

	def _sync_linked_sales_order_status(self):
		if self.sales_order and frappe.db.exists("Sales Order", self.sales_order):
			return
		if not self.sales_order or not frappe.db.exists("Plot Sales Order", self.sales_order):
			return
		so = frappe.get_doc("Plot Sales Order", self.sales_order)
		if so.docstatus == 1 and so.status in ("Open", "Converted"):
			so._sync_payment_status()

	# ------------------------------------------------------------------ #
	#  GL Entry helpers                                                    #
	# ------------------------------------------------------------------ #

	def _post_termination_journal_entry(self, settings):
		"""Forfeit ALL money received when a contract is terminated.

		The customer does not get a refund — the entire amount paid is
		recognised as income.

		Accounting:
		  Dr Customer Advances        (total paid — reduce liability)
		  Cr Forfeited Deposits Income (total paid — recognise income)
		"""
		if self.forfeiture_entry:
			return  # already posted — idempotent guard

		total_paid = flt(self.total_paid)
		if total_paid <= 0:
			return  # nothing was ever paid — nothing to forfeit

		je = frappe.get_doc({
			"doctype": "Journal Entry",
			"posting_date": today(),
			"company": settings.company,
			"voucher_type": "Journal Entry",
			"user_remark": (
				f"Contract termination — funds forfeited (no refund). "
				f"Contract {self.name}, Plot {self.plot}, Customer {self.customer}"
			),
			"accounts": [
				{
					"account": settings.customer_advance_account,
					"debit_in_account_currency": total_paid,
					"party_type": "Customer",
					"party": self.customer,
				},
				{
					"account": settings.forfeited_deposits_account,
					"credit_in_account_currency": total_paid,
				},
			],
		})
		je.insert(ignore_permissions=True)
		je.submit()
		self.db_set("forfeiture_entry", je.name)
		return je.name

	def _post_completion_entries(self, settings):
		"""Recognise revenue and record government fee on full contract payment.

		Only runs once (idempotent guard on government_fee_entry field).

		Accounting:
		  Dr Customer Advances     (selling_price — clears full liability)
		  Cr Government Payable    (government_fee_withheld)
		  Cr Plot Sales Revenue    (selling_price - government_fee_withheld)
		"""
		if self.government_fee_entry:
			return  # already posted

		selling_price = flt(self.selling_price)
		govt_fee      = flt(self.government_fee_withheld)
		net_revenue   = selling_price - govt_fee

		if selling_price <= 0:
			return

		accounts = [
			{
				"account": settings.customer_advance_account,
				"debit_in_account_currency": selling_price,
				"party_type": "Customer",
				"party": self.customer,
			},
		]

		if govt_fee > 0:
			accounts.append({
				"account": settings.government_payable_account,
				"credit_in_account_currency": govt_fee,
			})

		accounts.append({
			"account": settings.revenue_account,
			"credit_in_account_currency": net_revenue,
		})

		je = frappe.get_doc({
			"doctype": "Journal Entry",
			"posting_date": today(),
			"company": settings.company,
			"voucher_type": "Journal Entry",
			"user_remark": (
				f"Revenue recognition — Contract {self.name}, Plot {self.plot}, "
				f"Customer {self.customer}"
			),
			"accounts": accounts,
		})
		je.insert(ignore_permissions=True)
		je.submit()
		self.db_set("government_fee_entry", je.name)
		return je.name

	# ------------------------------------------------------------------ #
	#  Payment recording                                                   #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def record_payment(self, amount, payment_date, bank_account, reference_no=None):
		"""Create a Payment Entry against outstanding installments.

		If the payment amount exceeds one installment, it is allocated across
		multiple SIs (oldest due date first) in a single Payment Entry.

		Accounting:
		  SI submit  → Dr Accounts Receivable  / Cr Customer Advances
		  PE submit  → Dr Bank Account          / Cr Accounts Receivable
		  Net effect → Dr Bank Account          / Cr Customer Advances
		"""
		amount = flt(amount)
		if amount <= 0:
			frappe.throw("Payment amount must be greater than zero.")
		if self.docstatus != 1:
			frappe.throw("Contract must be submitted before recording payment.")
		if self.contract_status != "Ongoing":
			frappe.throw(f"Cannot record payment when contract status is '{self.contract_status}'.")

		settings = frappe.get_single("LMS Settings")
		self._validate_bank_account(bank_account, settings.company)
		self._check_duplicate_reference(reference_no)

		# Reload from DB so we have the sales_invoice links set during on_submit
		self.reload()

		# All unpaid rows ordered by installment number.
		pending_rows = [
			row for row in sorted(self.payment_schedule, key=lambda r: cint(r.installment_number or 0))
			if row.status != "Paid"
		]

		if not pending_rows:
			frappe.throw("No outstanding installments found for this contract.")

		# Build PE reference list — allocate across multiple SIs if payment > one installment
		references = []
		remaining = flt(amount)
		paid_from = None

		for row in pending_rows:
			if remaining <= 0:
				break

			si_name = self._ensure_row_sales_invoice(row, settings)
			si_doc = frappe.get_doc("Sales Invoice", si_name)

			if not paid_from:
				paid_from = si_doc.debit_to

			si_outstanding = flt(si_doc.outstanding_amount)
			if si_outstanding <= 0:
				continue

			allocate = min(remaining, si_outstanding)
			references.append({
				"reference_doctype": "Sales Invoice",
				"reference_name": si_doc.name,
				"allocated_amount": allocate,
			})
			remaining -= allocate

		if not references:
			frappe.throw("No outstanding amount found to allocate against.")
		if flt(remaining) > 0:
			frappe.throw(
				f"Payment amount exceeds outstanding installments by TZS {remaining:,.0f}. "
				"Please enter an amount up to the current outstanding total."
			)

		pe = frappe.get_doc({
			"doctype": "Payment Entry",
			"payment_type": "Receive",
			"posting_date": payment_date,
			"company": settings.company,
			"party_type": "Customer",
			"party": self.customer,
			"paid_from": paid_from,
			"paid_to": bank_account,
			"paid_amount": amount,
			"received_amount": amount,
			"reference_no": reference_no or "",
			"reference_date": payment_date,
			"remarks": f"Payment for {self.name} — Plot {self.plot}",
			"references": references,
		})
		pe.insert(ignore_permissions=True)
		pe.submit()

		self.sync_payment_status()
		self._sync_linked_sales_order_status()

		frappe.msgprint(
			f"Payment of TZS {amount:,.0f} recorded. Payment Entry: {pe.name}",
			indicator="green",
			alert=True,
		)
		return pe.name

	def sync_payment_status(self):
		"""Sync contract totals and status from either the legacy row invoices or the single SO invoice."""
		if self._get_standard_sales_order_invoice_name():
			return self._sync_standard_sales_order_payment_status()
		now = getdate(today())
		total_paid = 0.0

		for row in self.payment_schedule:
			if not row.sales_invoice:
				continue
			si = frappe.db.get_value(
				"Sales Invoice",
				row.sales_invoice,
				["outstanding_amount", "docstatus"],
				as_dict=True,
			)
			if not si:
				continue

			expected = flt(row.expected_amount)
			paid = max(0.0, expected - flt(si.outstanding_amount)) if si.docstatus == 1 else 0.0
			total_paid += paid

			if paid >= expected:
				new_status = "Paid"
			elif getdate(str(row.due_date)) < now:
				new_status = "Overdue"
			else:
				new_status = "Pending"

			# Keep in-memory rows aligned so sequential SI creation can use fresh statuses.
			row.paid_amount = paid
			row.status = new_status

			frappe.db.set_value(
				"Plot Contract Payment",
				row.name,
				{"paid_amount": paid, "status": new_status},
			)

		total_outstanding = flt(self.selling_price) - total_paid
		self.db_set("total_paid", total_paid)
		self.db_set("total_outstanding", total_outstanding)
		self.db_set("payment_progress", self._derive_payment_progress(total_paid, total_outstanding))

		if total_outstanding <= 0:
			self.db_set("contract_status", "Completed")
			frappe.db.set_value("Plot Master", self.plot, "status", "Ready for Handover")
			self._sync_land_acquisition_summary()

			# Post government fee JE (idempotent — skips if already done)
			settings = frappe.get_single("LMS Settings")
			self.reload()
			je_name = self._post_completion_entries(settings)

			msg = f"Contract fully paid. Plot {self.plot} marked as Ready for Handover."
			if je_name:
				msg += f" Government fee posted — Journal Entry: {je_name}."
			frappe.msgprint(msg, indicator="green", alert=True)

		elif total_paid > 0:
			self.db_set("contract_status", "Ongoing")

	def _sync_standard_sales_order_payment_status(self):
		"""Sync a contract driven by one standard Sales Order invoice."""
		invoice_name = self._get_standard_sales_order_invoice_name()
		if not invoice_name:
			return

		si = frappe.db.get_value(
			"Sales Invoice",
			invoice_name,
			["docstatus", "grand_total", "outstanding_amount"],
			as_dict=True,
		)
		if not si or si.docstatus != 1:
			return

		total_paid = max(0.0, flt(si.grand_total) - flt(si.outstanding_amount))
		total_outstanding = max(0.0, flt(si.outstanding_amount))

		if total_paid > 0 and self.docstatus == 0:
			self.submit()
			self.reload()

		self._sync_single_invoice_schedule_rows(total_paid)
		self.db_set("total_paid", total_paid)
		self.db_set("total_outstanding", total_outstanding)
		self.db_set("payment_progress", self._derive_payment_progress(total_paid, total_outstanding))

		so_doc = self._get_standard_sales_order_doc()
		if so_doc and so_doc.get("plot_application"):
			app_status = frappe.db.get_value("Plot Application", so_doc.plot_application, "status")
			if total_paid > 0 and app_status == "Paid":
				frappe.db.set_value("Plot Application", so_doc.plot_application, "status", "Converted")

		if total_outstanding <= 0 and self.docstatus == 1:
			self.db_set("contract_status", "Completed")
			current_plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
			if current_plot_status not in ("Delivered", "Title Closed"):
				frappe.db.set_value("Plot Master", self.plot, "status", "Ready for Handover")
			self._sync_land_acquisition_summary()

			settings = frappe.get_single("LMS Settings")
			self.reload()
			je_name = self._post_completion_entries(settings)

			msg = f"Contract fully paid. Plot {self.plot} marked as Ready for Handover."
			if je_name:
				msg += f" Government fee posted — Journal Entry: {je_name}."
			frappe.msgprint(msg, indicator="green", alert=True)

		elif total_paid > 0 and self.docstatus == 1:
			self.db_set("contract_status", "Ongoing")
			if frappe.db.get_value("Plot Master", self.plot, "status") == "Pending Advance":
				frappe.db.set_value("Plot Master", self.plot, "status", "Reserved")
				self._sync_land_acquisition_summary()

	def _sync_single_invoice_schedule_rows(self, total_paid):
		now = getdate(today())
		remaining_paid = flt(total_paid)

		for row in sorted(self.payment_schedule, key=lambda d: cint(d.installment_number or 0)):
			expected = flt(row.expected_amount)
			paid_amount = min(expected, max(remaining_paid, 0.0))
			remaining_paid -= paid_amount

			if paid_amount >= expected and expected > 0:
				status = "Paid"
			elif getdate(str(row.due_date)) < now:
				status = "Overdue"
			else:
				status = "Pending"

			row.paid_amount = paid_amount
			row.status = status
			frappe.db.set_value(
				"Plot Contract Payment",
				row.name,
				{"paid_amount": paid_amount, "status": status},
			)

	# ------------------------------------------------------------------ #
	#  Contract termination                                                #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def terminate_contract(self, reason):
		if self.contract_status != "Ongoing":
			frappe.throw("Only Ongoing contracts can be terminated.")
		if self.docstatus != 1:
			frappe.throw("Document must be submitted before it can be terminated.")
		if not reason or not str(reason).strip():
			frappe.throw("A termination reason is required.")

		settings = frappe.get_single("LMS Settings")

		# Reload to get current payment statuses from DB
		self.reload()

		# Cancel/delete all SIs that have not been paid
		self._cancel_unpaid_invoices()

		# Post forfeiture JE if booking fee was received
		je_name = self._post_termination_journal_entry(settings)

		frappe.db.set_value("Plot Master", self.plot, "status", "Available")
		self._sync_land_acquisition_summary()
		self.db_set("contract_status", "Terminated")
		self.db_set("termination_reason", str(reason).strip())

		msg = f"Contract terminated. Plot {self.plot} is now Available for new contracts."
		if je_name:
			total_paid = flt(self.total_paid)
			msg += f" TZS {total_paid:,.0f} paid by customer is forfeited (no refund) — Journal Entry: {je_name}."
		frappe.msgprint(msg, indicator="orange", alert=True)
