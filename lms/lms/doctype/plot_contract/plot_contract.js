frappe.ui.form.on('Plot Contract', {

	refresh: function(frm) {
		const colors = {
			'Draft': 'gray',
			'Active': 'blue',
			'Completed': 'green',
			'Cancelled': 'red'
		};
		const color = colors[frm.doc.contract_status] || 'gray';
		frm.page.set_indicator(frm.doc.contract_status, color);
	},

	plot: function(frm) {
		if (!frm.doc.plot) return;
		frappe.db.get_doc('Plot Master', frm.doc.plot)
			.then(plot_doc => {
				if (plot_doc.status !== 'Available') {
					frappe.msgprint({
						title: 'Plot Not Available',
						message: `Plot ${frm.doc.plot} has status "${plot_doc.status}". Only Available plots can be contracted.`,
						indicator: 'red'
					});
					frm.set_value('plot', '');
					return;
				}
				frm.set_value('selling_price', plot_doc.selling_price);
				apply_booking_fee(frm);
			});
	},

	contract_date: function(frm) {
		if (frm.doc.selling_price) {
			apply_booking_fee(frm);
		}
	}

});

function apply_booking_fee(frm) {
	Promise.all([
		frappe.db.get_single_value('LMS Settings', 'booking_fee_percent'),
		frappe.db.get_single_value('LMS Settings', 'payment_completion_days')
	]).then(([pct, days]) => {
		frm.set_value('booking_fee_percent', pct);
		const fee = (frm.doc.selling_price || 0) * (pct / 100);
		const balance = (frm.doc.selling_price || 0) - fee;
		frm.set_value('booking_fee_amount', fee);
		frm.set_value('balance_due', balance);

		if (frm.doc.contract_date) {
			const total_days = days || 90;
			const deadline = frappe.datetime.add_days(frm.doc.contract_date, total_days);
			frm.set_value('payment_deadline', deadline);
			build_payment_schedule(frm, fee, balance, total_days);
		}
	});
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
