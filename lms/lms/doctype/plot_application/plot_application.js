frappe.ui.form.on('Plot Application', {

	setup: function(frm) {
		frm.set_query('plot', function() {
			return {
				filters: { status: 'Available' }
			};
		});
	},

	refresh: function(frm) {
		// Status indicator colours
		const colors = {
			'Draft': 'gray',
			'Submitted': 'blue',
			'Paid': 'green',
			'Converted': 'purple',
			'Expired': 'red',
			'Cancelled': 'red'
		};
		const color = colors[frm.doc.status] || 'gray';
		frm.page.set_indicator(frm.doc.status, color);

		// "Record Fee Payment" button — only on Submitted applications
		if (frm.doc.docstatus === 1 && frm.doc.status === 'Submitted') {
			frm.add_custom_button('Record Fee Payment', function() {
				const openPaymentDialog = (defaultBankAccount) => {
					let d = new frappe.ui.Dialog({
						title: 'Record Application Fee Payment',
						fields: [
							{
								fieldname: 'fee_info',
								fieldtype: 'HTML',
								options: '<p style="margin-bottom:10px">Application Fee: <b>TZS ' +
									(frm.doc.application_fee || 0).toLocaleString() +
									'</b></p>'
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
								label: 'Received In (Bank/Cash)',
								options: 'Account',
								default: defaultBankAccount || '',
								description: 'Defaults from LMS Settings (Application Fee Receiving Account).',
								get_query: function() {
									return {
										filters: {
											is_group: 0,
											account_type: ['in', ['Bank', 'Cash']]
										}
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
						primary_action_label: 'Record Payment',
						primary_action: function(values) {
							frappe.call({
								method: 'record_fee_payment',
								doc: frm.doc,
								args: {
									payment_date: values.payment_date,
									bank_account: values.bank_account || '',
									reference_no: values.reference_no || ''
								},
								freeze: true,
								freeze_message: 'Recording payment...',
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
				};

				frappe.db.get_value('LMS Settings', 'LMS Settings', 'application_fee_receiving_account')
					.then(r => {
						const defaultBankAccount = (r.message && r.message.application_fee_receiving_account) || '';
						openPaymentDialog(defaultBankAccount);
					})
					.catch(() => openPaymentDialog(''));
				}, 'Actions');
			}

			// Optional quick path: create ERP Sales Order directly from a Paid application.
			if (frm.doc.docstatus === 1 && frm.doc.status === 'Paid' && !frm.doc.sales_order) {
				frm.add_custom_button('Create Sales Order', function() {
					frappe.call({
						method: 'create_sales_order',
						doc: frm.doc,
						args: { notify: 1 },
						freeze: true,
						freeze_message: 'Creating Sales Order...',
						callback: function(r) {
							if (!r.exc) {
								frm.reload_doc();
							}
						}
					});
				}, 'Actions');
			}

		// Link to Sales Order if one exists
		if (frm.doc.sales_order) {
			frm.add_custom_button('View Sales Order', function() {
				frappe.set_route('Form', 'Sales Order', frm.doc.sales_order);
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
						message: `Plot ${frm.doc.plot} has status "${plot_doc.status}". Only Available plots can be applied for.`,
						indicator: 'red'
					});
					frm.set_value('plot', '');
					frm.set_value('land_acquisition', '');
					frm.set_value('acquisition_name', '');
					return;
				}
				frm.set_value('land_acquisition', plot_doc.land_acquisition);
				frappe.db.get_value('Land Acquisition', plot_doc.land_acquisition, 'acquisition_name')
					.then(r => {
						frm.set_value('acquisition_name', r.message.acquisition_name || '');
					});
			});
	}

});
