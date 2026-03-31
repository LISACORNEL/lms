function copy_land_acquisition_to_items(frm) {
	(frm.doc.items || []).forEach((item) => {
		if ((item.land_acquisition || '') !== (frm.doc.land_acquisition || '')) {
			frappe.model.set_value(item.doctype, item.name, 'land_acquisition', frm.doc.land_acquisition || '');
		}
	});
}

function set_land_acquisition_on_new_row(frm, cdt, cdn) {
	if (!frm.doc.land_acquisition) return;

	const row = locals[cdt][cdn];
	if (!row.land_acquisition) {
		frappe.model.set_value(cdt, cdn, 'land_acquisition', frm.doc.land_acquisition);
	}
}

['Purchase Order', 'Purchase Invoice'].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		land_acquisition(frm) {
			copy_land_acquisition_to_items(frm);
		},

		items_add(frm, cdt, cdn) {
			set_land_acquisition_on_new_row(frm, cdt, cdn);
		},
	});
});

