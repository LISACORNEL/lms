frappe.ui.form.on('Plot Master', {

	setup: function(frm) {
		frm.set_query('land_acquisition', function() {
			return {
				filters: {
					status: ['in', ['Approved', 'Subdivided']]
				}
			};
		});
	},

	refresh: function(frm) {
		// Color the status indicator
		const colors = {
			'Available': 'green',
			'Pending Fee': 'orange',
			'Pending Advance': 'yellow',
			'Reserved': 'orange',
			'Ready for Handover': 'cyan',
			'Delivered': 'blue',
			'Title Closed': 'darkgreen'
		};
		const color = colors[frm.doc.status] || 'gray';
		frm.page.set_indicator(frm.doc.status, color);
	},

	land_acquisition: function(frm) {
		if (!frm.doc.land_acquisition) {
			frm.set_value('acquisition_name', '');
			frm.set_value('cost_per_sqm', 0);
			frm.set_value('allocated_cost', 0);
			return;
		}

		frappe.db.get_doc('Land Acquisition', frm.doc.land_acquisition)
			.then(doc => {
				if (!['Approved', 'Subdivided'].includes(doc.status)) {
					frappe.msgprint({
						title: 'Invalid Selection',
						message: `Land Acquisition ${frm.doc.land_acquisition} must be Approved or Subdivided (status: ${doc.status}).`,
						indicator: 'red'
					});
					frm.set_value('land_acquisition', '');
					frm.set_value('acquisition_name', '');
					frm.set_value('cost_per_sqm', 0);
					frm.set_value('allocated_cost', 0);
					return;
				}

				frm.set_value('acquisition_name', doc.acquisition_name || '');
				recalculate_costs(frm, doc);

				frappe.show_alert({
					message: `Land Acquisition selected: ${doc.name} - ${doc.acquisition_name || ''}. Cost per sqm has been loaded.`,
					indicator: 'blue'
				});
			});
	},

	plot_size_sqm: function(frm) {
		if (frm.doc.land_acquisition && frm.doc.plot_size_sqm) {
			frappe.db.get_doc('Land Acquisition', frm.doc.land_acquisition)
				.then(doc => recalculate_costs(frm, doc));
		} else {
			frm.set_value('allocated_cost', 0);
		}
	}
});

function recalculate_costs(frm, acquisition) {
	if (!acquisition || !acquisition.total_area_sqm) {
		frm.set_value('cost_per_sqm', 0);
		frm.set_value('allocated_cost', 0);
		return;
	}

	const cost_per_sqm = flt(acquisition.acquisition_cost_tzs) / flt(acquisition.total_area_sqm);
	const allocated = cost_per_sqm * flt(frm.doc.plot_size_sqm);

	frm.set_value('cost_per_sqm', cost_per_sqm);
	frm.set_value('allocated_cost', allocated);

	frm.set_df_property(
		'cost_per_sqm',
		'description',
		`${flt(acquisition.acquisition_cost_tzs).toLocaleString()} TZS ÷ ${flt(acquisition.total_area_sqm).toLocaleString()} sqm`
	);
	frm.set_df_property(
		'allocated_cost',
		'description',
		`${cost_per_sqm.toLocaleString()} TZS × ${flt(frm.doc.plot_size_sqm).toLocaleString()} sqm`
	);
	frm.set_df_property(
		'selling_price',
		'description',
		`Base cost is ${allocated.toLocaleString()} TZS. Enter your selling price above this amount.`
	);
}
