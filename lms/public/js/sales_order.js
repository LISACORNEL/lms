frappe.ui.form.on('Sales Order', {
	refresh(frm) {
		apply_lms_plot_application_query(frm);
	},

	onload(frm) {
		apply_lms_plot_application_query(frm);
	},

	plot_application(frm) {
		if (!frm.doc.plot_application) {
			return;
		}

		frappe.call({
			method: 'lms.sales_order_hooks.get_sales_order_defaults',
			args: {
				plot_application: frm.doc.plot_application,
			},
			freeze: true,
			freeze_message: __('Loading plot application details...'),
			callback: function(r) {
				const data = r.message || {};
				if (!data.plot) {
					return;
				}

				frm.set_value('company', data.company || frm.doc.company);
				frm.set_value('customer', data.customer || '');
				frm.set_value('plot', data.plot || '');
				frm.set_value('land_acquisition', data.land_acquisition || '');
				frm.set_value('acquisition_name', data.acquisition_name || '');
				frm.set_value('booking_fee_percent', data.booking_fee_percent || 0);
				frm.set_value('government_share_percent', data.government_share_percent || 0);
				frm.set_value('payment_completion_days', data.payment_completion_days || 0);
				frm.set_value('transaction_date', data.transaction_date || '');
				frm.set_value('payment_deadline', data.payment_deadline || '');
				frm.set_value('set_warehouse', data.set_warehouse || '');
				frm.doc.ignore_default_payment_terms_template = 1;

				if (data.item) {
					frm.clear_table('items');
					frm.add_child('items', data.item);
					frm.refresh_field('items');
				}

				if (Array.isArray(data.payment_schedule)) {
					frm.clear_table('payment_schedule');
					data.payment_schedule.forEach((row) => frm.add_child('payment_schedule', row));
					frm.refresh_field('payment_schedule');
				}
			},
		});
	},
});

function apply_lms_plot_application_query(frm) {
	frm.set_query('plot_application', function() {
		return {
			filters: {
				docstatus: 1,
				status: 'Paid',
				sales_order: '',
			},
		};
	});
}
