frappe.ui.form.on('Plot Sales Order', {

	setup: function(frm) {
		frm.set_query('plot', function() {
			return {
				filters: { status: ['in', ['Available', 'Reserved']] }
			};
		});

		frm.set_query('plot_application', function() {
			return {
				filters: {
					docstatus: 1,
					status: 'Paid',
					plot_sales_order: ['is', 'not set'],
					expiry_date: ['>=', frappe.datetime.get_today()]
				}
			};
		});
	},

	refresh: function(frm) {
		ensure_default_notes_template(frm);

		// Status indicator colours
		const colors = {
			'Draft': 'gray',
			'Open': 'yellow',
			'Converted': 'green',
			'Cancelled': 'red'
		};
		let display_status = frm.doc.status || 'Draft';
		if (frm.doc.docstatus === 0) display_status = 'Draft';
		if (frm.doc.docstatus === 2) display_status = 'Cancelled';
		const color = colors[display_status] || 'gray';
		frm.page.set_indicator(display_status, color);

		// "Receive Payment" button — available while submitted SO still has outstanding balance
		if (
			frm.doc.docstatus === 1 &&
			['Open', 'Converted'].includes(frm.doc.status) &&
			(Number(frm.doc.total_outstanding || 0) > 0)
		) {
			frm.add_custom_button('Receive Payment', function() {
				let d = new frappe.ui.Dialog({
					title: 'Receive Payment',
					fields: [
						{
							fieldname: 'amount',
							fieldtype: 'Float',
							label: 'Amount (TZS)',
							reqd: 1,
							default: frm.doc.total_outstanding || 0,
							description: 'Outstanding: TZS ' + (frm.doc.total_outstanding || 0).toLocaleString()
						},
						{
							fieldname: 'payment_date',
							fieldtype: 'Date',
							label: 'Payment Date',
							reqd: 1,
							default: frappe.datetime.get_today()
						},
						{
							fieldname: 'bank_account',
							fieldtype: 'Link',
							label: 'Bank Account',
							options: 'Account',
							reqd: 1,
							get_query: function() {
								return {
									filters: { account_type: 'Bank' }
								};
							}
						},
						{
							fieldname: 'reference_no',
							fieldtype: 'Data',
							label: 'Reference No',
							description: 'Bank receipt / transaction reference'
						}
					],
					primary_action_label: 'Receive Payment',
					primary_action: function(values) {
						frappe.call({
							method: 'receive_payment',
							doc: frm.doc,
							args: {
								amount: values.amount,
								payment_date: values.payment_date,
								bank_account: values.bank_account,
								reference_no: values.reference_no || ''
							},
							freeze: true,
							freeze_message: 'Processing payment...',
							callback: function(r) {
								if (!r.exc) {
									d.hide();
									frm.reload_doc();
								}
							}
						});
					}
				});
				d.show();
			}, 'Actions');
		}

		// Link to Plot Contract if one exists
		if (frm.doc.plot_contract) {
			frm.add_custom_button('View Plot Contract', function() {
				frappe.set_route('Form', 'Plot Contract', frm.doc.plot_contract);
			}, 'Actions');
		}
	},

	plot: function(frm) {
		if (!frm.doc.plot) {
			frm.set_value('land_acquisition', '');
			frm.set_value('acquisition_name', '');
			frm.set_value('selling_price', 0);
			return;
		}
			frappe.db.get_doc('Plot Master', frm.doc.plot)
				.then(plot_doc => {
					if (!['Available', 'Reserved'].includes(plot_doc.status)) {
						frappe.msgprint({
							title: 'Plot Not Available',
							message: `Plot ${frm.doc.plot} has status "${plot_doc.status}". Only Available or Reserved plots can be selected.`,
							indicator: 'red'
						});
					frm.set_value('plot', '');
					frm.set_value('land_acquisition', '');
					frm.set_value('acquisition_name', '');
					frm.set_value('selling_price', 0);
					return;
				}
				frm.set_value('land_acquisition', plot_doc.land_acquisition);
				frm.set_value('selling_price', plot_doc.selling_price);
				frappe.db.get_value('Land Acquisition', plot_doc.land_acquisition, 'acquisition_name')
					.then(r => {
						frm.set_value('acquisition_name', r.message.acquisition_name || '');
					});
					recalculate_amounts(frm);
				});
	},

	plot_application: function(frm) {
		if (!frm.doc.plot_application) return;

		frappe.db.get_doc('Plot Application', frm.doc.plot_application)
			.then(app => {
				if (app.docstatus !== 1 || app.status !== 'Paid') {
					frappe.msgprint({
						title: 'Invalid Plot Application',
						message: `Plot Application ${app.name} must be Submitted with status "Paid".`,
						indicator: 'red'
					});
					frm.set_value('plot_application', '');
					return;
				}
				if (app.plot_sales_order) {
					frappe.msgprint({
						title: 'Already Linked',
						message: `Plot Application ${app.name} is already linked to Sales Order ${app.plot_sales_order}.`,
						indicator: 'red'
					});
					frm.set_value('plot_application', '');
					return;
				}
				if (app.expiry_date && frappe.datetime.str_to_obj(app.expiry_date) < frappe.datetime.str_to_obj(frappe.datetime.get_today())) {
					frappe.msgprint({
						title: 'Application Expired',
						message: `Plot Application ${app.name} expired on ${app.expiry_date}.`,
						indicator: 'red'
					});
					frm.set_value('plot_application', '');
					return;
				}

				frm.set_value('customer', app.customer || '');
				frm.set_value('order_date', app.payment_date || frappe.datetime.get_today());
				frm.set_value('plot', app.plot || '');
				frm.set_value('land_acquisition', app.land_acquisition || '');
				frm.set_value('acquisition_name', app.acquisition_name || '');

				frappe.show_alert({
					message: `Loaded from ${app.name}. Set Booking Fee % and Government Share % to continue.`,
					indicator: 'blue'
				});
			});
	},

	booking_fee_percent: function(frm) {
		recalculate_amounts(frm);
	},

	payment_completion_days: function(frm) {
		recalculate_amounts(frm);
	},

	order_date: function(frm) {
		if (frm.doc.selling_price && frm.doc.booking_fee_percent) {
			recalculate_amounts(frm);
		}
	}

});

function recalculate_amounts(frm) {
	const selling_price = frm.doc.selling_price || 0;
	const pct = frm.doc.booking_fee_percent || 0;
	if (!selling_price || !pct) return;

	const fee = selling_price * (pct / 100);
	const balance = selling_price - fee;
	frm.set_value('booking_fee_amount', fee);
	frm.set_value('balance_due', balance);

	if (!frm.doc.order_date) return;

	const total_days = frm.doc.payment_completion_days || 90;
	const deadline = frappe.datetime.add_days(frm.doc.order_date, total_days);
	frm.set_value('payment_deadline', deadline);
	build_payment_schedule(frm, fee, balance, total_days);
}

function build_payment_schedule(frm, booking_fee, balance, total_days) {
	if (!frm.doc.order_date) return;

	frm.clear_table('payment_schedule');

	// Row 1: booking fee due on order date
	frm.add_child('payment_schedule', {
		installment_number: 1,
		due_date: frm.doc.order_date,
		expected_amount: booking_fee,
		paid_amount: 0,
		status: 'Pending'
	});

	// Row 2: remaining balance due on order date + payment completion days
	if (balance > 0) {
		frm.add_child('payment_schedule', {
			installment_number: 2,
			due_date: frappe.datetime.add_days(frm.doc.order_date, total_days),
			expected_amount: balance,
			paid_amount: 0,
			status: 'Pending'
		});
	}

	frm.refresh_field('payment_schedule');
}

function ensure_default_notes_template(frm) {
	if (!frm.is_new() || frm.doc.notes) return;

	frappe.db.get_doc('LMS Settings', 'LMS Settings')
		.then(settings => {
			if (frm.doc.notes) return;

			const unpaidDays = Number(settings.unpaid_application_expiry_days || 0);
			const paidDays = Number(settings.application_fee_validity_days || 0);
			const completionDays = Number(frm.doc.payment_completion_days || 90);

			const notes = [
				'Sales Order Payment Terms',
				'1. Plot Application fee is non-refundable.',
				`2. If the application fee is not paid, the application auto-cancels after ${unpaidDays} day(s).`,
				`3. After application fee payment, the reservation remains valid for ${paidDays} day(s).`,
				'4. Booking fee (advance) is the first payment under this Sales Order.',
				`5. Remaining balance/installments must be paid within ${completionDays} day(s) from Order Date, based on the schedule.`,
				'6. Late installments may be marked Overdue and can lead to contract cancellation/termination per LMS policy.'
			].join('\n');

			frm.set_value('notes', notes);
		})
		.catch(() => {
			// Keep form usable even if settings fetch fails.
		});
}
