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
			frm.set_value('selling_price_per_sqm_tzs', 0);
			frm.set_value('allocated_cost', 0);
			frm.set_value('selling_price', 0);
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
						message: `Land Acquisition selected: ${doc.name} - ${doc.acquisition_name || ''}. Plot pricing defaults have been loaded.`,
						indicator: 'blue'
					});
				});
		},

		plot_type: function(frm) {
			if (frm.doc.land_acquisition) {
				frappe.db.get_doc('Land Acquisition', frm.doc.land_acquisition)
					.then(doc => recalculate_costs(frm, doc));
			} else {
				frm.set_value('selling_price_per_sqm_tzs', 0);
				frm.set_value('selling_price', 0);
			}
		},

		plot_size_sqm: function(frm) {
			if (frm.doc.land_acquisition && frm.doc.plot_size_sqm) {
				frappe.db.get_doc('Land Acquisition', frm.doc.land_acquisition)
					.then(doc => recalculate_costs(frm, doc));
			} else {
				frm.set_value('allocated_cost', 0);
				frm.set_value('selling_price', 0);
			}
		}
	});

function recalculate_costs(frm, acquisition) {
	if (!acquisition || !acquisition.total_area_sqm) {
		frm.set_value('cost_per_sqm', 0);
		frm.set_value('selling_price_per_sqm_tzs', getSellingPricePerSqm(acquisition, frm.doc.plot_type));
		frm.set_value('allocated_cost', 0);
		frm.set_value('selling_price', 0);
		return;
	}

	const cost_per_sqm = flt(acquisition.cost_per_sqm_tzs) || (flt(acquisition.acquisition_cost_tzs) / flt(acquisition.total_area_sqm));
	const allocated = cost_per_sqm * flt(frm.doc.plot_size_sqm);
	const selling_price_per_sqm = getSellingPricePerSqm(acquisition, frm.doc.plot_type);
	const suggested_selling_price = selling_price_per_sqm
		? selling_price_per_sqm * flt(frm.doc.plot_size_sqm)
		: 0;

	frm.set_value('cost_per_sqm', cost_per_sqm);
	frm.set_value('selling_price_per_sqm_tzs', selling_price_per_sqm);
	frm.set_value('allocated_cost', allocated);
	frm.set_value('selling_price', suggested_selling_price);

	frm.set_df_property(
		'selling_price',
		'description',
		selling_price_per_sqm
			? `${selling_price_per_sqm.toLocaleString()} TZS × ${flt(frm.doc.plot_size_sqm).toLocaleString()} sqm = ${suggested_selling_price.toLocaleString()} TZS`
			: `Set the ${getRateLabelForPlotType(frm.doc.plot_type)} on the selected Land Acquisition to auto-populate this plot's selling price.`
	);
}

function getSellingPricePerSqm(acquisition, plotType) {
	const rateFieldByPlotType = {
		'Residential': 'residential_selling_price_per_sqm_tzs',
		'Commercial': 'commercial_selling_price_per_sqm_tzs',
		'Mixed-Use': 'mixed_use_selling_price_per_sqm_tzs',
	};

	const rateField = rateFieldByPlotType[plotType];
	if (rateField && flt(acquisition?.[rateField]) > 0) {
		return flt(acquisition[rateField]);
	}

	return flt(acquisition?.default_selling_price_per_sqm_tzs);
}

function getRateLabelForPlotType(plotType) {
	const labelByPlotType = {
		'Residential': 'Residential Rate per sqm (TZS)',
		'Commercial': 'Commercial Rate per sqm (TZS)',
		'Mixed-Use': 'Mixed-Use Rate per sqm (TZS)',
	};

	return labelByPlotType[plotType] || 'plot-type selling rate';
}
