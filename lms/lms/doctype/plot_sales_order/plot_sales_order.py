import frappe
from frappe.model.document import Document
from frappe.utils import flt, add_days, today, getdate, cint

from lms.lms.doctype.land_acquisition.land_acquisition import sync_land_acquisition_plot_summary
from lms.lms.doctype.plot_master.plot_master import PLOT_TYPE_TO_ITEM
from lms.lms.tcb import (
	generate_control_number,
	confirm_payment,
	register_reference_for_sales_order,
	decline_reference_for_sales_order,
)


class PlotSalesOrder(Document):

	# ------------------------------------------------------------------ #
	#  Validate                                                            #
	# ------------------------------------------------------------------ #

	def validate(self):
		self.fill_from_plot_application()
		self.validate_application_fee()
		self.validate_plot_available()
		self.fill_selling_price()
		self.calculate_financials()
		self.generate_payment_schedule()
		self.calculate_payment_summary()

	def fill_from_plot_application(self):
		"""Auto-fill SO core fields from linked Plot Application for manual SO creation."""
		if not self.plot_application:
			return

		app = frappe.db.get_value(
			"Plot Application",
			self.plot_application,
			["customer", "plot", "payment_date", "land_acquisition", "acquisition_name"],
			as_dict=True,
		)
		if not app:
			return

		if not self.customer:
			self.customer = app.customer
		if not self.plot:
			self.plot = app.plot
		if not self.order_date:
			self.order_date = app.payment_date or today()
		if not self.land_acquisition:
			self.land_acquisition = app.land_acquisition
		if not self.acquisition_name:
			self.acquisition_name = app.acquisition_name

	def validate_application_fee(self):
		"""Ensure the SO is linked to a valid paid Plot Application."""
		if not self.plot:
			return

		is_existing = bool(self.name and frappe.db.exists("Plot Sales Order", self.name))

		if not self.plot_application:
			if is_existing:
				return
			frappe.throw(
				"A paid Plot Application is required before creating a Sales Order. "
				"Please create and pay for a Plot Application first."
			)
		app = frappe.db.get_value(
			"Plot Application",
			self.plot_application,
			["status", "plot", "customer", "expiry_date", "docstatus", "plot_sales_order"],
			as_dict=True,
		)
		if not app or app.docstatus != 1:
			frappe.throw(f"Plot Application {self.plot_application} is not submitted.")

		if is_existing:
			if app.status not in ("Paid", "Converted"):
				frappe.throw(
					f"Plot Application {self.plot_application} has status '{app.status}'. "
					"Only Paid applications can be used for this Sales Order."
				)
		elif app.status != "Paid":
			frappe.throw(
				f"Plot Application {self.plot_application} has status '{app.status}'. "
				"Only Paid applications can be selected when creating a new Sales Order."
			)

		if app.plot != self.plot:
			frappe.throw(
				f"Plot Application {self.plot_application} is for plot {app.plot}, "
				f"but this Sales Order is for plot {self.plot}."
			)

		if self.customer and app.customer != self.customer:
			frappe.throw(
				f"Plot Application {self.plot_application} belongs to customer {app.customer}, "
				f"but this Sales Order is for customer {self.customer}."
			)

		if app.expiry_date and getdate(app.expiry_date) < getdate(today()):
			frappe.throw(
				f"Plot Application {self.plot_application} expired on {app.expiry_date}. "
				"Create a new application before raising a Sales Order."
			)

		if app.plot_sales_order and app.plot_sales_order != self.name:
			frappe.throw(
				f"Plot Application {self.plot_application} is already linked to "
				f"Sales Order {app.plot_sales_order}."
			)

	def validate_plot_available(self):
		if not self.plot:
			return
		if not frappe.db.exists("Plot Sales Order", self.name):
			plot_status = frappe.db.get_value("Plot Master", self.plot, "status")
			# Plot can be Available or Reserved (reserved by the application)
			if plot_status not in ("Available", "Reserved"):
				frappe.throw(
					f"Plot {self.plot} is not Available (current status: {plot_status}). "
					"Only Available or Reserved (via application) plots can be added to a Sales Order."
				)
			active_open = frappe.db.exists("Plot Sales Order", {
				"plot": self.plot,
				"docstatus": 1,
				"status": "Open",
			})
			if active_open:
				frappe.throw(
					f"Plot {self.plot} already has an active Sales Order ({active_open}). "
					"The existing Sales Order must be cancelled first."
				)

			# Converted SOs only block a new SO while their linked contract
			# is still active (Ongoing/Completed).
			converted_orders = frappe.db.get_all(
				"Plot Sales Order",
				filters={
					"plot": self.plot,
					"docstatus": 1,
					"status": "Converted",
					"name": ("!=", self.name),
				},
				fields=["name", "plot_contract"],
			)
			for row in converted_orders:
				if not row.plot_contract:
					frappe.throw(
						f"Plot {self.plot} is blocked by converted Sales Order {row.name} "
						"with no linked contract."
					)

				contract_state = frappe.db.get_value(
					"Plot Contract",
					row.plot_contract,
					["docstatus", "contract_status"],
					as_dict=True,
				)
				if not contract_state:
					frappe.throw(
						f"Plot {self.plot} is blocked by converted Sales Order {row.name}. "
						f"Linked contract {row.plot_contract} was not found."
					)
				if contract_state.docstatus == 0:
					frappe.throw(
						f"Plot {self.plot} already has a draft contract ({row.plot_contract}) "
						f"from Sales Order {row.name}."
					)
				if contract_state.docstatus == 1 and contract_state.contract_status in ("Ongoing", "Completed"):
					frappe.throw(
						f"Plot {self.plot} already has an active contract "
						f"({row.plot_contract}, status: {contract_state.contract_status})."
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
				self.acquisition_name = frappe.db.get_value(
					"Land Acquisition", plot_data.land_acquisition, "acquisition_name"
				) or ""

	def calculate_financials(self):
		if flt(self.selling_price) > 0 and flt(self.booking_fee_percent) > 0:
			self.booking_fee_amount = flt(self.selling_price) * flt(self.booking_fee_percent) / 100
			self.balance_due = flt(self.selling_price) - self.booking_fee_amount
		if self.order_date and flt(self.payment_completion_days) > 0:
			self.payment_deadline = add_days(self.order_date, int(self.payment_completion_days))

	def generate_payment_schedule(self):
		"""Build the two-row payment schedule:
		  Row 1 — Booking fee, due on order date
		  Row 2 — Remaining balance, due on order date + payment_completion_days
		Only runs in Draft — once submitted, rows are managed by _sync_payment_status().
		"""
		if self.docstatus == 1:
			return
		if not flt(self.selling_price) or not flt(self.booking_fee_percent):
			return
		if not self.order_date:
			return

		booking_fee = flt(self.booking_fee_amount) or (
			flt(self.selling_price) * flt(self.booking_fee_percent) / 100
		)
		balance = flt(self.selling_price) - booking_fee
		total_days = int(self.payment_completion_days or 90)

		self.payment_schedule = []

		# Row 1 — booking fee, due on order date
		self.append("payment_schedule", {
			"installment_number": 1,
			"due_date": self.order_date,
			"expected_amount": booking_fee,
			"paid_amount": 0,
			"status": "Pending",
		})

		# Row 2 — remaining balance, due on order date + total days
		if balance > 0:
			self.append("payment_schedule", {
				"installment_number": 2,
				"due_date": add_days(self.order_date, total_days),
				"expected_amount": balance,
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
		"""Reserve plot, generate control number, create SIs, and create draft contract."""
		frappe.db.set_value("Plot Master", self.plot, "status", "Reserved")
		self._sync_land_acquisition_summary()
		self.db_set("status", "Open")
		self._link_plot_application()

		# Generate one partner reference for this Sales Order and register it with TCB
		control_no = generate_control_number(self.name)
		self.db_set("control_number", control_no)
		registration = register_reference_for_sales_order(self.name, control_no)
		if not registration.get("ok"):
			frappe.throw(registration.get("message") or "TCB reference registration failed.")

		# Keep SI creation tied to confirmed payments.
		# On submit we only prepare the draft contract/schedule.
		contract_name = self._ensure_draft_contract()
		mode = registration.get("mode") or "Off"
		registration_note = registration.get("message") or ""

		frappe.msgprint(
			f"Sales Order submitted. TCB Control Number: <b>{control_no}</b>. "
			f"Reference registration mode: <b>{mode}</b>. {registration_note} "
			f"Draft contract <b>{contract_name}</b> prepared. "
			"Customer can use this number to make payments at TCB bank.",
			indicator="blue",
			alert=True,
		)

	def on_cancel(self):
		"""Release plot, clean draft contract, and cancel outstanding SIs."""
		if self.plot_contract and frappe.db.exists("Plot Contract", self.plot_contract):
			contract = frappe.get_doc("Plot Contract", self.plot_contract)
			if contract.docstatus == 1:
				frappe.throw(
					f"Cannot cancel Sales Order {self.name} because linked contract "
					f"{contract.name} is already submitted. Terminate/cancel the contract first."
				)
				frappe.delete_doc("Plot Contract", contract.name, ignore_permissions=True)
				self.db_set("plot_contract", "")

		self._decline_tcb_reference_if_required()
		self._unlink_plot_application()
		frappe.db.set_value("Plot Master", self.plot, "status", "Available")
		self._sync_land_acquisition_summary()
		self.db_set("status", "Cancelled")
		self._cancel_sales_invoices()

	def _decline_tcb_reference_if_required(self):
		"""Decline registered control number at TCB when an unpaid SO is cancelled."""
		if not self.control_number:
			return
		if self._has_any_payment_received():
			return

		result = decline_reference_for_sales_order(
			sales_order_name=self.name,
			control_number=self.control_number,
		)
		if result.get("block_cancel"):
			frappe.throw(result.get("message") or "TCB decline failed and policy blocks cancellation.")
		if not result.get("ok") and result.get("message"):
			frappe.msgprint(
				f"Warning: Sales Order cancelled locally, but TCB reference decline failed. "
				f"Reason: {result.get('message')}",
				indicator="orange",
				alert=True,
			)

	def _link_plot_application(self):
		"""Bind this submitted SO to its source Plot Application."""
		if not self.plot_application:
			return

		frappe.db.sql(
			"select name from `tabPlot Application` where name=%s for update",
			(self.plot_application,),
		)
		app = frappe.db.get_value(
			"Plot Application",
			self.plot_application,
			["docstatus", "status", "plot_sales_order"],
			as_dict=True,
		)
		if not app or app.docstatus != 1:
			frappe.throw(f"Plot Application {self.plot_application} is not submitted.")
		if app.plot_sales_order and app.plot_sales_order != self.name:
			frappe.throw(
				f"Plot Application {self.plot_application} is already linked to "
				f"Sales Order {app.plot_sales_order}."
			)
		if app.status not in ("Paid", "Converted"):
			frappe.throw(
				f"Plot Application {self.plot_application} has status '{app.status}'. "
				"Only Paid applications can be linked to a Sales Order."
			)

		updates = {"plot_sales_order": self.name}
		if app.status == "Paid":
			updates["status"] = "Converted"
		frappe.db.set_value("Plot Application", self.plot_application, updates)

	def _unlink_plot_application(self):
		"""Release Plot Application link when SO is cancelled."""
		if not self.plot_application:
			return

		app = frappe.db.get_value(
			"Plot Application",
			self.plot_application,
			["plot_sales_order", "status", "expiry_date"],
			as_dict=True,
		)
		if not app or app.plot_sales_order != self.name:
			return

		updates = {"plot_sales_order": ""}
		if app.status == "Converted":
			if app.expiry_date and getdate(app.expiry_date) < getdate(today()):
				updates["status"] = "Expired"
			else:
				updates["status"] = "Paid"
		frappe.db.set_value("Plot Application", self.plot_application, updates)

	def _sync_land_acquisition_summary(self):
		land_acquisition = frappe.db.get_value("Plot Master", self.plot, "land_acquisition")
		if land_acquisition:
			sync_land_acquisition_plot_summary(land_acquisition)

	def _build_contract_schedule_rows(self):
		rows = []
		for row in self.payment_schedule:
			rows.append({
				"installment_number": row.installment_number,
				"due_date": row.due_date,
				"expected_amount": row.expected_amount,
				"paid_amount": row.paid_amount,
				"status": row.status,
				"sales_invoice": row.sales_invoice,
			})
		return rows

	def _ensure_draft_contract(self):
		"""Create a draft Plot Contract (if missing) linked to this Sales Order."""
		if self.plot_contract and frappe.db.exists("Plot Contract", self.plot_contract):
			return self.plot_contract

		contract = frappe.get_doc({
			"doctype": "Plot Contract",
			"customer": self.customer,
			"plot": self.plot,
			"contract_date": self.order_date or today(),
			"payment_completion_days": self.payment_completion_days,
			"booking_fee_percent": self.booking_fee_percent,
			"government_share_percent": self.government_share_percent,
			"selling_price": self.selling_price,
			"sales_order": self.name,
			"payment_schedule": self._build_contract_schedule_rows(),
		})
		contract.flags.from_sales_order = True
		contract.insert(ignore_permissions=True)  # Draft (docstatus=0)
		self.db_set("plot_contract", contract.name)
		return contract.name

	def _activate_contract_from_first_payment(self):
		"""Submit the draft contract on first payment and sync its status."""
		contract_name = self._ensure_draft_contract()
		contract = frappe.get_doc("Plot Contract", contract_name)
		if contract.docstatus == 0:
			contract.submit()
		contract.reload()
		contract.sync_payment_status()
		self.db_set("status", "Converted")
		return contract.name

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
			  and pcp.parenttype = 'Plot Sales Order'
			  and pcp.parent = %s
			limit 1
			""",
			(reference_no, self.name),
			as_dict=True,
		)
		if existing:
			frappe.throw(
				f"Duplicate payment reference '{reference_no}'. "
				f"Payment Entry {existing[0].name} already exists for this Sales Order."
			)

	def _has_any_payment_received(self):
		"""True when any linked SI has been partially/fully paid."""
		for row in self.payment_schedule:
			if not row.sales_invoice:
				continue
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
		return flt(self.total_paid) > 0

	# ------------------------------------------------------------------ #
	#  Sales Invoice helpers                                               #
	# ------------------------------------------------------------------ #

	def _create_sales_invoices(self):
		"""Deprecated helper (kept for compatibility)."""
		return

	def _ensure_row_sales_invoice(self, row, settings):
		"""Create and link SI for a payment row if missing; return SI name."""
		if row.sales_invoice:
			return row.sales_invoice

		plot_type = frappe.db.get_value("Plot Master", self.plot, "plot_type")
		item_code = PLOT_TYPE_TO_ITEM.get(plot_type)

		if not item_code:
			frappe.throw(f"No item mapped for plot type '{plot_type}'.")
		description = (
			f"Advance Payment — Plot {self.plot} ({self.name})"
			if cint(row.installment_number or 0) == 1
			else f"Installment {row.installment_number} — Plot {self.plot} ({self.name})"
		)

		si_name = self._make_sales_invoice(
			item_code=item_code,
			amount=flt(row.expected_amount),
			due_date=row.due_date,
			description=description,
			settings=settings,
		)
		frappe.db.set_value("Plot Contract Payment", row.name, "sales_invoice", si_name)
		row.sales_invoice = si_name
		frappe.logger("lms").info(
			f"Created SI {si_name} for SO {self.name} "
			f"(installment #{row.installment_number}) after payment confirmation"
		)
		return si_name

	def _make_sales_invoice(self, item_code, amount, due_date, description, settings):
		"""Create and submit a Sales Invoice posting to Customer Advances (liability)."""
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
		si.submit()
		return si.name

	def _cancel_sales_invoices(self):
		"""Cancel/delete unpaid SIs and keep links for already settled invoices."""
		for row in self.payment_schedule:
			if not row.sales_invoice:
				continue
			si_name = row.sales_invoice
			si_doc = frappe.get_doc("Sales Invoice", si_name)
			if si_doc.docstatus == 0:
				frappe.delete_doc("Sales Invoice", si_name, ignore_permissions=True)
				frappe.db.set_value("Plot Contract Payment", row.name, "sales_invoice", "")
				row.sales_invoice = ""
			elif si_doc.docstatus == 1 and flt(si_doc.outstanding_amount) > 0:
				si_doc.cancel()
				frappe.db.set_value("Plot Contract Payment", row.name, "sales_invoice", "")
				row.sales_invoice = ""

	# ------------------------------------------------------------------ #
	#  TCB Payment handling                                                #
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def receive_payment(self, amount, payment_date, bank_account, reference_no=None, skip_tcb_confirmation=0):
		"""TCB payment confirmation handler.

		Called when TCB bank confirms a payment has been received
		against this Sales Order's control number.

		Flow:
		  1. TCB confirms payment (dummy: always succeeds)
		  2. Payment Entry created: Dr Bank / Cr AR
		  3. Outstanding SIs reduced / marked Paid
		  4. If this is the first payment → Plot Contract auto-created
		  5. If Plot Contract exists → sync its payment status
		     (revenue recognition fires automatically when outstanding = 0)

		Accounting (per payment):
		  SI submit (already done on SO submit) → Dr AR / Cr Customer Advances
		  PE submit  → Dr Bank / Cr AR
		  Net effect → Dr Bank / Cr Customer Advances (liability increases)

		Revenue is only recognised in the Plot Contract when outstanding = 0.
		"""
		amount = flt(amount)
		if amount <= 0:
			frappe.throw("Payment amount must be greater than zero.")
		if self.docstatus != 1:
			frappe.throw("Sales Order must be submitted before recording payment.")
		if self.status not in ("Open", "Converted"):
			frappe.throw(f"Cannot record payment when Sales Order status is '{self.status}'.")
		if not self.control_number:
			frappe.throw("Control number is missing on this Sales Order.")
		skip_tcb_confirmation = cint(skip_tcb_confirmation)

		# Confirm with TCB (or skip when payment is already confirmed by callback/reconciliation)
		settings = frappe.get_single("LMS Settings")
		self._validate_bank_account(bank_account, settings.company)
		self._check_duplicate_reference(reference_no)
		if skip_tcb_confirmation:
			tcb_response = {
				"confirmed": True,
				"reference": reference_no or self.control_number,
			}
		else:
			tcb_response = confirm_payment(
				control_number=self.control_number,
				amount=amount,
				payment_date=payment_date,
				reference_no=reference_no,
			)
			if not tcb_response.get("confirmed"):
				frappe.throw("TCB payment confirmation failed. Please try again.")

		self.reload()

		# All unpaid rows ordered by due date (oldest first)
		pending_rows = [
			row for row in sorted(self.payment_schedule, key=lambda r: cint(r.installment_number or 0))
			if row.status != "Paid"
		]

		if not pending_rows:
			frappe.throw("No outstanding installments found for this Sales Order.")

		# Build Payment Entry references — allocate across multiple SIs if needed
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

		# Create and submit Payment Entry
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
			"reference_no": tcb_response.get("reference") or self.control_number,
			"reference_date": payment_date,
			"remarks": f"TCB Payment — {self.name} / Plot {self.plot} / Control No: {self.control_number}",
			"references": references,
		})
		pe.insert(ignore_permissions=True)
		pe.submit()

		# Sync payment status on this Sales Order
		self._sync_payment_status()

		contract_docstatus = None
		if self.plot_contract and frappe.db.exists("Plot Contract", self.plot_contract):
			contract_docstatus = frappe.db.get_value("Plot Contract", self.plot_contract, "docstatus")

		# Activate draft contract on first confirmed payment
		if not self.plot_contract or contract_docstatus == 0:
			contract_name = self._activate_contract_from_first_payment()
			frappe.msgprint(
				f"Payment of TZS {amount:,.0f} confirmed by TCB. "
				f"Plot Contract <b>{contract_name}</b> is now active (Ongoing). "
				f"Payment Entry: {pe.name}",
				indicator="green",
				alert=True,
			)
		else:
			# Sync revenue recognition on the contract
			contract = frappe.get_doc("Plot Contract", self.plot_contract)
			contract.reload()
			contract.sync_payment_status()
			frappe.msgprint(
				f"Payment of TZS {amount:,.0f} confirmed by TCB. "
				f"Payment Entry: {pe.name}",
				indicator="green",
				alert=True,
			)

		return pe.name

	def _sync_payment_status(self):
		"""Update payment schedule rows and totals based on SI outstanding amounts."""
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

			# Update both DB and in-memory state so convert_to_contract()
			# copies the correct paid amounts when creating the Plot Contract.
			row.paid_amount = paid
			row.status = new_status
			frappe.db.set_value(
				"Plot Contract Payment",
				row.name,
				{"paid_amount": paid, "status": new_status},
			)

		self.db_set("total_paid", total_paid)
		self.db_set("total_outstanding", flt(self.selling_price) - total_paid)

	# ------------------------------------------------------------------ #
	#  Convert to Contract                                                 #
	# ------------------------------------------------------------------ #

	def convert_to_contract(self):
		"""Backward-compatible wrapper: activate draft contract from this SO."""
		return self._activate_contract_from_first_payment()
