import frappe
from frappe.model.document import Document
from frappe.utils import flt, add_days, today, getdate

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

	def validate_plot_available(self):
		if not self.plot:
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
				"contract_status": ["in", ["Active", "Completed"]],
			})
			if active:
				frappe.throw(
					f"Plot {self.plot} already has an active contract ({active}). "
					"The existing contract must be terminated or completed first."
				)

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

	def calculate_financials(self):
		if flt(self.selling_price) > 0 and flt(self.booking_fee_percent) > 0:
			self.booking_fee_amount = flt(self.selling_price) * flt(self.booking_fee_percent) / 100
			self.balance_due = flt(self.selling_price) - self.booking_fee_amount
		if self.contract_date and flt(self.payment_completion_days) > 0:
			self.payment_deadline = add_days(self.contract_date, int(self.payment_completion_days))

	def generate_payment_schedule(self):
		"""Rebuild schedule rows from contract parameters.
		Only runs while the document is still in Draft — once submitted,
		rows are managed by sync_payment_status() after each payment."""
		if self.docstatus == 1:
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

		if balance > 0:
			# Build due-day offsets: monthly steps up to (not including) total_days,
			# then always end exactly on total_days.
			# e.g. 50 days → [30, 50]   90 days → [30, 60, 90]   45 days → [30, 45]
			due_day_offsets = []
			d = 30
			while d < total_days:
				due_day_offsets.append(d)
				d += 30
			due_day_offsets.append(total_days)

			num_installments = len(due_day_offsets)
			per_installment = int(balance / num_installments)

			for i, offset in enumerate(due_day_offsets):
				is_last = (i == num_installments - 1)
				amount = (
					balance - (per_installment * (num_installments - 1))
					if is_last
					else flt(per_installment)
				)
				self.append("payment_schedule", {
					"installment_number": i + 2,
					"due_date": add_days(self.contract_date, offset),
					"expected_amount": amount,
					"paid_amount": 0,
					"status": "Pending",
				})

	def calculate_payment_summary(self):
		self.total_contract_value = flt(self.selling_price)
		total_paid = sum(flt(row.paid_amount) for row in self.payment_schedule)
		self.total_paid = total_paid
		self.total_outstanding = flt(self.selling_price) - total_paid
		if flt(self.government_share_percent) > 0:
			self.government_fee_withheld = (
				flt(self.selling_price) * flt(self.government_share_percent) / 100
			)

	# ------------------------------------------------------------------ #
	#  Submit / Cancel                                                     #
	# ------------------------------------------------------------------ #

	def on_submit(self):
		frappe.db.set_value("Plot Master", self.plot, "status", "Reserved")
		self.db_set("contract_status", "Active")
		self._create_sales_invoices()

	def on_cancel(self):
		frappe.db.set_value("Plot Master", self.plot, "status", "Available")
		self.db_set("contract_status", "Cancelled")
		self._cancel_sales_invoices()

	# ------------------------------------------------------------------ #
	#  Sales Invoice helpers                                               #
	# ------------------------------------------------------------------ #

	def _create_sales_invoices(self):
		settings = frappe.get_single("LMS Settings")
		plot_type = frappe.db.get_value("Plot Master", self.plot, "plot_type")
		item_code = PLOT_TYPE_TO_ITEM.get(plot_type)

		if not item_code:
			frappe.throw(f"No item mapped for plot type '{plot_type}'.")
		if not self.payment_schedule:
			frappe.throw("Payment schedule is empty — cannot create invoices.")

		for idx, row in enumerate(self.payment_schedule):
			is_booking = (idx == 0)
			label = (
				f"Booking Fee — Plot {self.plot} ({self.name})"
				if is_booking
				else f"Installment {row.installment_number} — Plot {self.plot} ({self.name})"
			)
			si_name = self._make_sales_invoice(
				item_code=item_code,
				amount=flt(row.expected_amount),
				due_date=row.due_date,
				description=label,
				settings=settings,
				submit=is_booking,   # booking fee SI submitted immediately; installments stay Draft
			)
			frappe.db.set_value("Plot Contract Payment", row.name, "sales_invoice", si_name)
			if is_booking:
				self.db_set("booking_fee_invoice", si_name)

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
			si_doc = frappe.get_doc("Sales Invoice", row.sales_invoice)
			if si_doc.docstatus == 1:
				si_doc.cancel()
			elif si_doc.docstatus == 0:
				frappe.delete_doc("Sales Invoice", row.sales_invoice, ignore_permissions=True)

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
			si_doc = frappe.get_doc("Sales Invoice", row.sales_invoice)
			if si_doc.docstatus == 0:
				frappe.delete_doc("Sales Invoice", row.sales_invoice, ignore_permissions=True)
			elif si_doc.docstatus == 1:
				if flt(si_doc.outstanding_amount) > 0:
					si_doc.cancel()

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
		"""Split government's share on full contract payment.

		Only runs once (idempotent guard on government_fee_entry field).

		Accounting:
		  Dr Customer Advances     (reduce liability)
		  Cr Government Payable    (record obligation to government)
		"""
		if self.government_fee_entry:
			return  # already posted

		govt_fee = flt(self.government_fee_withheld)
		if govt_fee <= 0:
			return

		je = frappe.get_doc({
			"doctype": "Journal Entry",
			"posting_date": today(),
			"company": settings.company,
			"voucher_type": "Journal Entry",
			"user_remark": (
				f"Government fee — Contract {self.name}, Plot {self.plot}, "
				f"Customer {self.customer}"
			),
			"accounts": [
				{
					"account": settings.customer_advance_account,
					"debit_in_account_currency": govt_fee,
					"party_type": "Customer",
					"party": self.customer,
				},
				{
					"account": settings.government_payable_account,
					"credit_in_account_currency": govt_fee,
				},
			],
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

		settings = frappe.get_single("LMS Settings")

		# Reload from DB so we have the sales_invoice links set during on_submit
		self.reload()

		# All unpaid rows that have a linked SI, ordered by due date (oldest first)
		pending_rows = [
			row for row in sorted(self.payment_schedule, key=lambda r: str(r.due_date or ""))
			if row.sales_invoice and row.status != "Paid"
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

			si_doc = frappe.get_doc("Sales Invoice", row.sales_invoice)

			# Submit Draft SIs before allocating against them
			if si_doc.docstatus == 0:
				si_doc.submit()
				si_doc.reload()

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

		frappe.msgprint(
			f"Payment of TZS {amount:,.0f} recorded. Payment Entry: {pe.name}",
			indicator="green",
			alert=True,
		)
		return pe.name

	def sync_payment_status(self):
		"""Re-read each SI's outstanding amount and update child rows + contract totals."""
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

			frappe.db.set_value(
				"Plot Contract Payment",
				row.name,
				{"paid_amount": paid, "status": new_status},
			)

		total_outstanding = flt(self.selling_price) - total_paid
		self.db_set("total_paid", total_paid)
		self.db_set("total_outstanding", total_outstanding)

		if total_outstanding <= 0:
			self.db_set("contract_status", "Completed")
			frappe.db.set_value("Plot Master", self.plot, "status", "Delivered")

			# Post government fee JE (idempotent — skips if already done)
			settings = frappe.get_single("LMS Settings")
			self.reload()
			je_name = self._post_completion_entries(settings)

			msg = f"Contract fully paid. Plot {self.plot} marked as Delivered."
			if je_name:
				msg += f" Government fee posted — Journal Entry: {je_name}."
			frappe.msgprint(msg, indicator="green", alert=True)

	# ------------------------------------------------------------------ #
	#  Contract termination                                                #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def terminate_contract(self, reason):
		if self.contract_status != "Active":
			frappe.throw("Only Active contracts can be terminated.")
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
		self.db_set("contract_status", "Terminated")
		self.db_set("termination_reason", str(reason).strip())

		msg = f"Contract terminated. Plot {self.plot} is now Available for new contracts."
		if je_name:
			total_paid = flt(self.total_paid)
			msg += f" TZS {total_paid:,.0f} paid by customer is forfeited (no refund) — Journal Entry: {je_name}."
		frappe.msgprint(msg, indicator="orange", alert=True)
