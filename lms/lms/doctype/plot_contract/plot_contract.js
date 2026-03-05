frappe.ui.form.on('Plot Contract', {

	setup: function(frm) {
		frm.set_query('plot', function() {
			return {
				filters: { status: 'Available' }
			};
		});
	},

	refresh: function(frm) {
		const colors = {
			'Draft': 'gray',
			'Ongoing': 'yellow',
			'Completed': 'green',
			'Cancelled': 'red',
			'Terminated': 'orange'
		};
		const color = colors[frm.doc.contract_status] || 'gray';
		frm.page.set_indicator(frm.doc.contract_status, color);

		if (frm.doc.docstatus === 1 && frm.doc.contract_status === 'Ongoing') {
			frm.add_custom_button('Record Payment', function() {
				let d = new frappe.ui.Dialog({
					title: 'Record Payment',
					fields: [
						{
							fieldname: 'amount',
							fieldtype: 'Float',
							label: 'Amount (TZS)',
							reqd: 1
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
							label: 'Reference No'
						}
					],
					primary_action_label: 'Record Payment',
					primary_action: function(values) {
						frappe.call({
							method: 'record_payment',
							doc: frm.doc,
							args: {
								amount: values.amount,
								payment_date: values.payment_date,
								bank_account: values.bank_account,
								reference_no: values.reference_no || ''
							},
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

			frm.add_custom_button('Terminate Contract', function() {
				frappe.prompt(
					{
						fieldname: 'reason',
						fieldtype: 'Long Text',
						label: 'Termination Reason',
						reqd: 1,
						description: 'Explain why this contract is being terminated (e.g. buyer failed to complete payment)'
					},
					function(values) {
						frappe.call({
							method: 'terminate_contract',
							doc: frm.doc,
							args: { reason: values.reason },
							callback: function() {
								frm.reload_doc();
							}
						});
					},
					'Terminate Contract',
					'Terminate'
				);
			}, 'Actions');
		}

		if (frm.doc.docstatus === 1 && frm.doc.contract_status === 'Completed') {
			frm.add_custom_button('Create Handover Document', function() {
				frappe.new_doc('Plot Handover', {
					contract: frm.doc.name,
					customer: frm.doc.customer,
					plot: frm.doc.plot,
					acquisition_name: frm.doc.acquisition_name,
					land_acquisition: frm.doc.land_acquisition,
					contract_date: frm.doc.contract_date,
					selling_price: frm.doc.selling_price
				});
			}, 'Actions');
		}
	},

	plot: function(frm) {
		if (!frm.doc.plot) {
			frm.set_value('land_acquisition', '');
			frm.set_value('acquisition_name', '');
			return;
		}
		frappe.db.get_doc('Plot Master', frm.doc.plot)
			.then(plot_doc => {
				if (plot_doc.status !== 'Available') {
					frappe.msgprint({
						title: 'Plot Not Available',
						message: `Plot ${frm.doc.plot} has status "${plot_doc.status}". Only Available plots can be contracted.`,
						indicator: 'red'
					});
					frm.set_value('plot', '');
					frm.set_value('land_acquisition', '');
					frm.set_value('acquisition_name', '');
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

	booking_fee_percent: function(frm) {
		recalculate_amounts(frm);
	},

	payment_completion_days: function(frm) {
		recalculate_amounts(frm);
	},

	contract_date: function(frm) {
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

	if (!frm.doc.contract_date) return;

	const total_days = frm.doc.payment_completion_days || 90;
	const deadline = frappe.datetime.add_days(frm.doc.contract_date, total_days);
	frm.set_value('payment_deadline', deadline);
	build_payment_schedule(frm, fee, balance, total_days);
}

function build_payment_schedule(frm, booking_fee, balance, total_days) {
	if (!frm.doc.contract_date) return;

	frm.clear_table('payment_schedule');

	// Row 1: booking fee due on contract date
	frm.add_child('payment_schedule', {
		installment_number: 1,
		due_date: frm.doc.contract_date,
		expected_amount: booking_fee,
		paid_amount: 0,
		status: 'Pending'
	});

	if (balance > 0) {
		// Build due-day offsets: monthly steps up to (not including) total_days,
		// then always land exactly on total_days.
		// e.g. 50 days → [30, 50]   90 days → [30, 60, 90]   45 days → [30, 45]
		const due_day_offsets = [];
		let d = 30;
		while (d < total_days) {
			due_day_offsets.push(d);
			d += 30;
		}
		due_day_offsets.push(total_days);

		const num_installments = due_day_offsets.length;
		const per_installment = Math.floor(balance / num_installments);

		due_day_offsets.forEach((offset, i) => {
			const is_last = (i === num_installments - 1);
			const amount = is_last
				? balance - (per_installment * (num_installments - 1))
				: per_installment;
			frm.add_child('payment_schedule', {
				installment_number: i + 2,
				due_date: frappe.datetime.add_days(frm.doc.contract_date, offset),
				expected_amount: amount,
				paid_amount: 0,
				status: 'Pending'
			});
		});
	}

	frm.refresh_field('payment_schedule');
}
