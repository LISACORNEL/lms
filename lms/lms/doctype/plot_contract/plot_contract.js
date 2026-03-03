frappe.ui.form.on('Plot Contract', {

	refresh: function(frm) {
		const colors = {
			'Draft': 'gray',
			'Active': 'blue',
			'Completed': 'green',
			'Cancelled': 'red',
			'Terminated': 'orange'
		};
		const color = colors[frm.doc.contract_status] || 'gray';
		frm.page.set_indicator(frm.doc.contract_status, color);

		if (frm.doc.docstatus === 1 && frm.doc.contract_status === 'Active') {
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
	},

	plot: function(frm) {
		if (!frm.doc.plot) {
			frm.set_value('land_acquisition', '');
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
					return;
				}
				frm.set_value('land_acquisition', plot_doc.land_acquisition);
				frm.set_value('selling_price', plot_doc.selling_price);
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
		// Split balance into monthly installments
		const num_months = Math.round(total_days / 30);
		const per_installment = Math.floor(balance / num_months);

		for (let i = 1; i <= num_months; i++) {
			const due_date = frappe.datetime.add_days(frm.doc.contract_date, i * 30);
			// Last installment gets any remainder from rounding
			const amount = (i === num_months)
				? balance - (per_installment * (num_months - 1))
				: per_installment;

			frm.add_child('payment_schedule', {
				installment_number: i + 1,
				due_date: due_date,
				expected_amount: amount,
				paid_amount: 0,
				status: 'Pending'
			});
		}
	}

	frm.refresh_field('payment_schedule');
}
