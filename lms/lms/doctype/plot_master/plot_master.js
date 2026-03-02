frappe.ui.form.on('Plot Master', {

	refresh: function(frm) {
		// Color the status indicator
		const colors = {
			'Available': 'green',
			'Reserved': 'orange',
			'Delivered': 'blue',
			'Title Closed': 'darkgreen'
		};
		const color = colors[frm.doc.status] || 'gray';
		frm.page.set_indicator(frm.doc.status, color);
	},

	land_acquisition: function(frm) {
		// When a Land Acquisition is selected, fill the allocated cost
		if (!frm.doc.land_acquisition) return;

		frappe.db.get_doc('Land Acquisition', frm.doc.land_acquisition)
			.then(doc => {
				if (doc.status !== 'Approved') {
					frappe.msgprint({
						title: 'Invalid Selection',
						message: `Land Acquisition ${frm.doc.land_acquisition} is not Approved (status: ${doc.status}). Select an Approved Land Acquisition.`,
						indicator: 'red'
					});
					frm.set_value('land_acquisition', '');
					return;
				}
				// Cost will be calculated server-side based on plot_size_sqm
				// Just show a message to fill in the plot size
				frappe.show_alert({
					message: 'Land Acquisition selected. Enter the plot size to calculate allocated cost.',
					indicator: 'blue'
				});
			});
	},

	plot_size_sqm: function(frm) {
		// Recalculate allocated cost when plot size changes
		if (frm.doc.land_acquisition && frm.doc.plot_size_sqm) {
			frappe.db.get_doc('Land Acquisition', frm.doc.land_acquisition)
				.then(doc => {
					if (doc.total_area_sqm > 0) {
						const cost_per_sqm = doc.acquisition_cost_tzs / doc.total_area_sqm;
						const allocated = cost_per_sqm * frm.doc.plot_size_sqm;
						frm.set_value('allocated_cost', allocated);
						frm.set_df_property('allocated_cost', 'description',
							`${doc.acquisition_cost_tzs.toLocaleString()} TZS ÷ ${doc.total_area_sqm} sqm × ${frm.doc.plot_size_sqm} sqm`
						);
					}
				});
		}
	}
});
