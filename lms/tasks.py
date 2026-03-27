import frappe
from frappe.utils import today, getdate, flt, add_days


def hourly():
    """Entry point for LMS hourly scheduled jobs."""
    jobs = [
        ("auto_reconcile_tcb_payments", auto_reconcile_tcb_payments),
    ]
    for job_name, job_fn in jobs:
        try:
            job_fn()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS hourly: {job_name} failed",
            )


def daily():
    """Entry point for all LMS daily scheduled jobs.

    Each job is wrapped in try/except so one failure does not block the rest.
    Execution order matters:
      1. Send 24h warnings for applications nearing expiry
      2. Cancel stale unpaid applications  (free up plots held by non-payers)
      3. Expire paid applications past deadline  (also cancels unpaid linked Sales Orders)
      4. Cancel stale LMS Sales Orders with no first payment  (backstop cleanup)
      5. Mark overdue installments  (flag missed payments against the single invoice schedule)
      6. Auto-terminate contracts with overdue installments
      7. Sync stale payment statuses  (fix drift from external SI/PE actions)
    """
    jobs = [
        ("notify_plot_applications_expiring_in_24h", notify_plot_applications_expiring_in_24h),
        ("auto_cancel_stale_unpaid_applications", auto_cancel_stale_unpaid_applications),
        ("auto_expire_paid_applications_past_deadline", auto_expire_paid_applications_past_deadline),
        ("auto_cancel_stale_open_sales_orders_without_payment", auto_cancel_stale_open_sales_orders_without_payment),
        ("auto_mark_overdue_installments", auto_mark_overdue_installments),
        ("auto_terminate_contracts_with_overdue_installments", auto_terminate_contracts_with_overdue_installments),
        ("auto_sync_stale_payment_statuses", auto_sync_stale_payment_statuses),
    ]
    for job_name, job_fn in jobs:
        try:
            job_fn()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS daily: {job_name} failed",
            )


# ── Plot Application jobs ──────────────────────────────────────────────


def notify_plot_applications_expiring_in_24h():
    """Create in-app alerts 24h before application validity ends.

    Covers both:
      - Before payment: Submitted applications nearing unpaid timeout
      - After payment: Paid applications nearing reservation expiry
    """
    settings = frappe.get_single("LMS Settings")
    target_date = add_days(today(), 1)
    unpaid_days = int(settings.unpaid_application_expiry_days or 3)
    paid_days = int(settings.application_fee_validity_days or 7)

    unpaid_due = frappe.db.sql(
        """
        select
            name,
            plot,
            customer,
            application_date,
            %s as unpaid_validity_days,
            date_add(application_date, interval %s day) as expiry_date
        from `tabPlot Application`
        where docstatus = 1
          and status = 'Submitted'
          and date_add(application_date, interval %s day) = %s
        order by application_date asc, name asc
        """,
        (unpaid_days, unpaid_days, unpaid_days, target_date),
        as_dict=True,
    )

    paid_due = frappe.db.sql(
        """
        select
            name,
            plot,
            customer,
            date_add(payment_date, interval %s day) as expiry_date
        from `tabPlot Application`
        where docstatus = 1
          and status = 'Paid'
          and payment_date is not null
          and date_add(payment_date, interval %s day) = %s
        order by payment_date asc, name asc
        """,
        (paid_days, paid_days, target_date),
        as_dict=True,
    )

    if not unpaid_due and not paid_due:
        return

    recipient_roles = ("Sales", "Finance", "Administrator")
    recipients = frappe.db.sql(
        """
        select distinct u.name
        from `tabUser` u
        inner join `tabHas Role` hr
            on hr.parent = u.name and hr.parenttype = 'User'
        where u.enabled = 1
          and u.user_type = 'System User'
          and u.name != 'Guest'
          and hr.role in (%s, %s, %s)
        order by u.name asc
        """,
        recipient_roles,
        pluck=True,
    )
    if not recipients:
        frappe.logger("lms").info(
            "24h application validity alert skipped: no eligible recipients in roles "
            f"{', '.join(recipient_roles)} (target {target_date})"
        )
        return

    subject = f"LMS: Plot Application validity alert ({target_date})"
    message = _build_application_validity_alert_message(target_date, unpaid_due, paid_due)
    primary_doc = unpaid_due[0].name if unpaid_due else paid_due[0].name

    sent_count = 0
    start_of_day = f"{today()} 00:00:00"
    for user in recipients:
        already_sent = frappe.db.sql(
            """
            select name
            from `tabNotification Log`
            where for_user = %s
              and type = 'Alert'
              and subject = %s
              and creation >= %s
            limit 1
            """,
            (user, subject, start_of_day),
            as_dict=True,
        )
        if already_sent:
            continue

        try:
            frappe.get_doc(
                {
                    "doctype": "Notification Log",
                    "for_user": user,
                    "type": "Alert",
                    "subject": subject,
                    "email_content": message,
                    "document_type": "Plot Application",
                    "document_name": primary_doc,
                    "from_user": "Administrator",
                }
            ).insert(ignore_permissions=True)
            sent_count += 1
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS: Failed to create 24h validity alert for user {user}",
            )

    if sent_count:
        frappe.db.commit()

    frappe.logger("lms").info(
        f"24h application validity alert processed for {target_date}: "
        f"unpaid={len(unpaid_due)}, paid={len(paid_due)}, users_notified={sent_count}"
    )


def _build_application_validity_alert_message(target_date, unpaid_due, paid_due):
    lines = [
        f"Plot Applications expiring in 24 hours (target date: {target_date})",
        "",
    ]

    if unpaid_due:
        lines.append(f"Before payment (Submitted): {len(unpaid_due)}")
        for row in unpaid_due:
            lines.append(
                f"- {row.name} | Plot: {row.plot} | Customer: {row.customer} | "
                f"Expiry: {row.expiry_date}"
            )
        lines.append("")

    if paid_due:
        lines.append(f"After payment (Paid): {len(paid_due)}")
        for row in paid_due:
            lines.append(
                f"- {row.name} | Plot: {row.plot} | Customer: {row.customer} | "
                f"Expiry: {row.expiry_date}"
            )

    return "\n".join(lines)


def auto_cancel_stale_unpaid_applications():
    """Cancel Submitted (unpaid) Plot Applications that exceeded the allowed waiting period.

    Reads 'Unpaid Application Expiry (Days)' from LMS Settings.
    Calls doc.cancel() on each stale application so that:
      - docstatus is properly set to 2 (Cancelled)
      - on_cancel fires (sets status = 'Cancelled')
      - Frappe audit trail / timeline is preserved
    """
    settings = frappe.get_single("LMS Settings")
    expiry_days = int(settings.unpaid_application_expiry_days or 3)
    cutoff_date = add_days(today(), -expiry_days)

    stale_apps = frappe.db.get_all(
        "Plot Application",
        filters={
            "docstatus": 1,
            "status": "Submitted",
            "application_date": ["<=", cutoff_date],
        },
        fields=["name", "plot", "customer"],
    )

    cancelled_count = 0
    for app in stale_apps:
        try:
            doc = frappe.get_doc("Plot Application", app.name)
            doc.flags.ignore_permissions = True
            doc.cancel()
            cancelled_count += 1
            frappe.logger("lms").info(
                f"Auto-cancelled unpaid Plot Application {app.name} "
                f"(plot {app.plot}, customer {app.customer}) — "
                f"no payment received within {expiry_days} days"
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS: Failed to cancel Plot Application {app.name}",
            )

    if cancelled_count:
        frappe.db.commit()


def auto_expire_paid_applications_past_deadline():
    """Expire Paid Plot Applications whose reservation period has elapsed.

    Calls doc.cancel() with flags._cancellation_reason = 'Expired' so that
    on_cancel sets status to 'Expired' (not 'Cancelled') and releases the plot.

    Non-refundable — no reversal of the application fee SI.
    """
    settings = frappe.get_single("LMS Settings")
    today_date = getdate(today())
    validity_days = int(settings.application_fee_validity_days or 7)

    expired_apps = frappe.db.sql(
        """
        select
            name,
            plot,
            date_add(payment_date, interval %s day) as expiry_date
        from `tabPlot Application`
        where docstatus = 1
          and status = 'Paid'
          and payment_date is not null
          and date_add(payment_date, interval %s day) < %s
        order by payment_date asc, name asc
        """,
        (validity_days, validity_days, today_date),
        as_dict=True,
    )

    expired_count = 0
    for app in expired_apps:
        try:
            doc = frappe.get_doc("Plot Application", app.name)
            doc.flags.ignore_permissions = True
            doc.flags._cancellation_reason = "Expired"
            doc.cancel()
            expired_count += 1
            frappe.logger("lms").info(
                f"Auto-expired Plot Application {app.name} "
                f"(plot {app.plot}, expired {app.expiry_date}) — "
                f"reservation deadline passed, plot released"
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS: Failed to expire Plot Application {app.name}",
            )

    if expired_count:
        frappe.db.commit()


# ── Plot Contract jobs ─────────────────────────────────────────────────


def auto_cancel_stale_open_sales_orders_without_payment():
    """Cancel LMS ERP Sales Orders with no first payment after the paid-app window.

    This is a backstop in case a paid Plot Application somehow remains open past
    its validity window without `auto_expire_paid_applications_past_deadline`
    cleaning it up first.
    """
    settings = frappe.get_single("LMS Settings")
    validity_days = int(settings.application_fee_validity_days or 7)
    today_date = getdate(today())

    stale_orders = frappe.db.sql(
        """
        select
            so.name,
            so.plot,
            so.customer,
            so.plot_application,
            date_add(app.payment_date, interval %s day) as expiry_date
        from `tabSales Order` so
        inner join `tabPlot Application` app
            on app.name = so.plot_application
        where so.docstatus = 1
          and ifnull(so.plot_application, '') != ''
          and app.docstatus = 1
          and app.status = 'Paid'
          and app.payment_date is not null
          and date_add(app.payment_date, interval %s day) < %s
        order by app.payment_date asc, so.name asc
        """,
        (validity_days, validity_days, today_date),
        as_dict=True,
    )

    cancelled_count = 0
    for row in stale_orders:
        try:
            so = frappe.get_doc("Sales Order", row.name)
            invoice_name = so.get("plot_sales_invoice")
            if invoice_name and frappe.db.exists("Sales Invoice", invoice_name):
                outstanding, grand_total = frappe.db.get_value(
                    "Sales Invoice",
                    invoice_name,
                    ["outstanding_amount", "grand_total"],
                )
                if flt(outstanding) < flt(grand_total):
                    continue
            if frappe.db.get_value("Plot Application", row.plot_application, "docstatus") != 1:
                continue
            so.flags.ignore_permissions = True
            so.cancel()
            cancelled_count += 1
            frappe.logger("lms").info(
                f"Auto-cancelled stale Sales Order {row.name} (plot {row.plot}, customer {row.customer}) "
                f"— no payment received within {validity_days} days"
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS: Failed to auto-cancel Sales Order {row.name}",
            )

    if cancelled_count:
        frappe.db.commit()


def auto_submit_due_installment_invoices():
    """Submit Draft installment Sales Invoices whose due date has arrived.

    On the due date each installment SI is submitted so it:
      - Appears in the customer's outstanding invoices
      - Feeds the Accounts Receivable ageing report
      - Can receive a Payment Entry via Record Payment
    """
    today_date = getdate(today())

    due_rows = frappe.db.get_all(
        "Plot Contract Payment",
        filters={
            "parenttype": "Plot Contract",
            "due_date": ["<=", today_date],
            "sales_invoice": ["!=", ""],
            "status": ["!=", "Paid"],
        },
        fields=["name", "parent", "sales_invoice", "due_date", "installment_number"],
    )

    affected_contracts = set()

    for row in due_rows:
        si_docstatus = frappe.db.get_value("Sales Invoice", row.sales_invoice, "docstatus")
        if si_docstatus != 0:
            continue  # Already submitted or cancelled — skip

        # Only process contracts that are still Ongoing
        contract_status = frappe.db.get_value("Plot Contract", row.parent, "contract_status")
        if contract_status != "Ongoing":
            continue

        try:
            si_doc = frappe.get_doc("Sales Invoice", row.sales_invoice)
            si_doc.submit()
            affected_contracts.add(row.parent)
            frappe.logger("lms").info(
                f"Auto-submitted SI {row.sales_invoice} "
                f"(installment #{row.installment_number} of {row.parent}, "
                f"due {row.due_date})"
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS: Failed to submit SI {row.sales_invoice} for {row.parent}",
            )

    # Commit before syncing so the submitted SI state is visible
    if affected_contracts:
        frappe.db.commit()

    for contract_name in affected_contracts:
        try:
            contract = frappe.get_doc("Plot Contract", contract_name)
            contract.sync_payment_status()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS: Failed to sync payment status for {contract_name}",
            )


def auto_mark_overdue_installments():
    """Flip Pending installment rows to Overdue once their due date has passed.

    In the single-invoice flow, rows are milestone trackers only. This job
    marks them overdue once their due date passes and the milestone is still unpaid.
    """
    today_date = getdate(today())

    pending_past_due = frappe.db.get_all(
        "Plot Contract Payment",
        filters={
            "parenttype": "Plot Contract",
            "due_date": ["<", today_date],
            "status": "Pending",
        },
        fields=["name", "parent", "sales_invoice", "installment_number", "expected_amount", "paid_amount"],
    )

    overdue_count = 0
    for row in pending_past_due:
        contract_status = frappe.db.get_value("Plot Contract", row.parent, "contract_status")
        if contract_status != "Ongoing":
            continue

        # If row is already fully paid, do not mark overdue.
        if flt(row.paid_amount) >= flt(row.expected_amount):
            continue

        should_mark_overdue = False
        if not row.sales_invoice:
            # No SI exists yet and due date already passed -> overdue by timeline.
            should_mark_overdue = True
        else:
            si_info = frappe.db.get_value(
                "Sales Invoice",
                row.sales_invoice,
                ["docstatus", "outstanding_amount"],
                as_dict=True,
            )
            if not si_info:
                should_mark_overdue = True
            elif si_info.docstatus == 1 and flt(si_info.outstanding_amount) > 0:
                should_mark_overdue = True
            elif si_info.docstatus == 0:
                should_mark_overdue = True

        if should_mark_overdue:
            frappe.db.set_value("Plot Contract Payment", row.name, "status", "Overdue")
            overdue_count += 1
            frappe.logger("lms").info(
                f"Marked installment #{row.installment_number} of {row.parent} "
                f"as Overdue (SI {row.sales_invoice or 'not-created'})"
            )

    if overdue_count:
        frappe.db.commit()


def auto_terminate_contracts_with_overdue_installments():
    """Terminate Ongoing contracts that now contain overdue installments."""
    overdue_contracts = frappe.db.sql(
        """
        select distinct parent
        from `tabPlot Contract Payment`
        where parenttype = 'Plot Contract'
          and status = 'Overdue'
        """,
        as_dict=True,
    )

    terminated_count = 0
    for row in overdue_contracts:
        try:
            contract = frappe.get_doc("Plot Contract", row.parent)
            if contract.docstatus != 1 or contract.contract_status != "Ongoing":
                continue
            contract.flags.ignore_permissions = True
            contract.terminate_contract(
                reason=(
                    "Automatic termination: one or more installments became overdue "
                    "beyond the agreed payment timeline."
                )
            )
            terminated_count += 1
            frappe.logger("lms").info(
                f"Auto-terminated contract {contract.name} due to overdue installment(s)"
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS: Failed to auto-terminate contract {row.parent}",
            )

    if terminated_count:
        frappe.db.commit()


def auto_sync_stale_payment_statuses():
    """Resync LMS contracts against the single ERP Sales Order invoice."""
    affected_contracts = set()

    standard_rows = frappe.db.get_all(
        "Sales Order",
        filters={
            "docstatus": 1,
            "plot_contract": ["!=", ""],
            "plot_sales_invoice": ["!=", ""],
        },
        fields=["name", "plot_contract", "plot_sales_invoice"],
    )

    for row in standard_rows:
        if not frappe.db.exists("Plot Contract", row.plot_contract):
            continue
        if not frappe.db.exists("Sales Invoice", row.plot_sales_invoice):
            continue

        si = frappe.db.get_value(
            "Sales Invoice",
            row.plot_sales_invoice,
            ["docstatus", "grand_total", "outstanding_amount"],
            as_dict=True,
        )
        if not si or si.docstatus != 1:
            continue

        total_paid = max(0.0, flt(si.grand_total) - flt(si.outstanding_amount))
        contract = frappe.db.get_value(
            "Plot Contract",
            row.plot_contract,
            ["docstatus", "total_paid", "total_outstanding", "contract_status"],
            as_dict=True,
        )
        if not contract:
            continue

        if (
            contract.docstatus == 0 and total_paid > 0
            or flt(contract.total_paid) != total_paid
            or flt(contract.total_outstanding) != flt(si.outstanding_amount)
            or (flt(si.outstanding_amount) <= 0 and contract.contract_status != "Completed")
            or (total_paid > 0 and flt(si.outstanding_amount) > 0 and contract.contract_status != "Ongoing")
        ):
            affected_contracts.add(row.plot_contract)

    for contract_name in affected_contracts:
        try:
            contract = frappe.get_doc("Plot Contract", contract_name)
            if contract.docstatus in (0, 1):
                contract.sync_payment_status()
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"LMS: Failed stale sync for Plot Contract {contract_name}",
            )

    if affected_contracts:
        frappe.db.commit()


def auto_reconcile_tcb_payments():
    """Pull TCB reconciliation records and optionally auto-apply missing payments."""
    from lms.lms.tcb import run_tcb_reconciliation_job

    result = run_tcb_reconciliation_job()
    status = result.get("status") or ("Success" if result.get("ok") else "Failed")
    message = result.get("message") or "No message returned."

    if status == "Failed":
        frappe.log_error(message=message, title="LMS: TCB reconciliation failed")
    else:
        frappe.logger("lms").info(f"TCB reconciliation: {message}")
