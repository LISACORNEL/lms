frappe.ui.form.on('Land Acquisition', {
    refresh: function(frm) {
        if (frm.doc.docstatus === 0 && frm.doc.approval_state === "Pending Approval") {
            frm.dashboard.set_headline_alert(
                'This Land Acquisition is waiting for approval',
                'orange'
            );
        } else if (frm.doc.docstatus === 1 && frm.doc.status === "Approved") {
            frm.dashboard.set_headline_alert(
                'This Land Acquisition is Approved',
                'green'
            );
        } else if (frm.doc.docstatus === 1 && frm.doc.status === "Subdivided") {
            frm.dashboard.set_headline_alert(
                'This Land Acquisition is Approved and already subdivided',
                'blue'
            );
        }

        // Populate plot count summary (only for saved docs)
        if (frm.doc.name && !frm.doc.__islocal) {
            refresh_plot_counts(frm);
            refresh_cost_summary(frm);

            frm.add_custom_button('Purchase Order', function() {
                frappe.route_options = {
                    land_acquisition: frm.doc.name
                };
                frappe.new_doc('Purchase Order');
            }, 'Create');

            frm.add_custom_button('Purchase Invoice', function() {
                frappe.route_options = {
                    land_acquisition: frm.doc.name
                };
                frappe.new_doc('Purchase Invoice');
            }, 'Create');
        }
    }
});

function refresh_plot_counts(frm) {
    frappe.call({
        method: 'lms.lms.doctype.land_acquisition.land_acquisition.sync_land_acquisition_plot_summary',
        args: { land_acquisition: frm.doc.name },
        callback: function(r) {
            const s = r.message || {};
            frm.doc.total_plots = Number(s.total_plots || 0);
            frm.doc.available_plots = Number(s.available_plots || 0);
            frm.doc.reserved_plots = Number(s.reserved_plots || 0);
            frm.doc.delivered_plots = Number(s.delivered_plots || 0);
            if (s.status) {
                frm.doc.status = s.status;
            }
            frm.refresh_field('status');
            frm.refresh_field('total_plots');
            frm.refresh_field('available_plots');
            frm.refresh_field('reserved_plots');
            frm.refresh_field('delivered_plots');
        }
    });
}

function refresh_cost_summary(frm) {
    frappe.call({
        method: 'lms.lms.doctype.land_acquisition.land_acquisition.sync_land_acquisition_cost_summary',
        args: { land_acquisition: frm.doc.name },
	        callback: function(r) {
	            const s = r.message || {};
	            frm.doc.seller_purchase_amount_tzs = Number(s.seller_purchase_amount_tzs || 0);
	            frm.doc.committed_cost_tzs = Number(s.committed_cost_tzs || 0);
	            frm.doc.additional_project_cost_tzs = Number(s.additional_project_cost_tzs || 0);
	            frm.doc.acquisition_cost_tzs = Number(s.acquisition_cost_tzs || 0);
	            frm.doc.cost_per_sqm_tzs = Number(s.cost_per_sqm_tzs || 0);
	            frm.doc.total_acquisition_cost = Number(s.total_acquisition_cost || 0);
	            frm.doc.supplier_invoice_total_tzs = Number(s.supplier_invoice_total_tzs || 0);
	            frm.doc.acquisition_currency = s.acquisition_currency || frm.doc.acquisition_currency;
	            frm.doc.exchange_rate = Number(s.exchange_rate || 1);
	            frm.refresh_field('seller_purchase_amount_tzs');
	            frm.refresh_field('committed_cost_tzs');
	            frm.refresh_field('additional_project_cost_tzs');
	            frm.refresh_field('acquisition_cost_tzs');
	            frm.refresh_field('cost_per_sqm_tzs');
	            frm.refresh_field('total_acquisition_cost');
	            frm.refresh_field('supplier_invoice_total_tzs');
	            frm.refresh_field('acquisition_currency');
	            frm.refresh_field('exchange_rate');
	            if (flt(frm.doc.total_area_sqm) > 0) {
	                frm.set_df_property(
	                    'cost_per_sqm_tzs',
	                    'description',
	                    `${flt(frm.doc.acquisition_cost_tzs).toLocaleString()} TZS ÷ ${flt(frm.doc.total_area_sqm).toLocaleString()} sqm`
	                );
	            }
	            render_land_seller_summary(frm, s.land_seller_rows || []);
	            render_other_supplier_summary(frm, s.other_supplier_rows || []);
	        }
	    });
	}

	function render_land_seller_summary(frm, rows) {
	    const wrapper = frm.get_field('land_seller_summary_html')?.$wrapper;
	    if (!wrapper) return;

	    const currency = frm.doc.acquisition_currency || 'TZS';
	    const escape_html = (value) => frappe.utils.escape_html(String(value || ''));

	    if (!rows.length) {
	        wrapper.html(`
	            <div class="text-muted" style="padding: 8px 0;">
	                No land seller Purchase Orders or posted Purchase Invoices are tagged to this Land Acquisition yet.
	            </div>
	        `);
	        return;
	    }

	    const body = rows.map((row) => {
	        const purchaseOrder = row.purchase_order
	            ? `<a href="/app/purchase-order/${encodeURIComponent(row.purchase_order)}">${escape_html(row.purchase_order)}</a>`
	            : '<span class="text-muted">-</span>';
	        const purchaseInvoice = row.purchase_invoice
	            ? row.purchase_invoice.split(',').map((value) => {
	                const trimmed = value.trim();
	                return `<a href="/app/purchase-invoice/${encodeURIComponent(trimmed)}">${escape_html(trimmed)}</a>`;
	              }).join(', ')
	            : '<span class="text-muted">-</span>';

	        return `
	            <tr>
	                <td style="padding: 12px; vertical-align: middle;">${escape_html(row.supplier_name)}</td>
	                <td style="padding: 12px; vertical-align: middle;">${purchaseOrder}</td>
	                <td style="padding: 12px; vertical-align: middle;">${purchaseInvoice}</td>
	                <td class="text-right" style="padding: 12px; vertical-align: middle;">${format_currency(row.amount_tzs || 0, currency)}</td>
	                <td style="padding: 12px; vertical-align: middle;">${escape_html(row.status_label)}</td>
	            </tr>
	        `;
	    }).join('');

	    wrapper.html(`
	        <div class="table-responsive">
	            <table class="table table-bordered" style="margin-bottom: 0; font-size: 14px;">
	                <thead style="background-color: #f8f9fa;">
	                    <tr>
	                        <th style="padding: 12px; font-weight: 600; width: 20%;">Supplier</th>
	                        <th style="padding: 12px; font-weight: 600; width: 20%;">Purchase Order</th>
	                        <th style="padding: 12px; font-weight: 600; width: 25%;">Purchase Invoice</th>
	                        <th class="text-right" style="padding: 12px; font-weight: 600; width: 15%;">Amount</th>
	                        <th style="padding: 12px; font-weight: 600; width: 20%;">Status</th>
	                    </tr>
	                </thead>
	                <tbody>${body}</tbody>
	            </table>
	        </div>
	    `);
	}

	function render_other_supplier_summary(frm, rows) {
	    const wrapper = frm.get_field('supplier_invoice_summary_html')?.$wrapper;
	    if (!wrapper) return;

    const currency = frm.doc.acquisition_currency || 'TZS';
    const escape_html = (value) => frappe.utils.escape_html(String(value || ''));

	    if (!rows.length) {
	        wrapper.html(`
	            <div class="text-muted" style="padding: 8px 0;">
	                No non-land-seller Purchase Orders or posted Purchase Invoices are tagged to this Land Acquisition yet.
	            </div>
	        `);
	        return;
	    }

    const body = rows.map((row) => {
        const purchaseOrder = row.purchase_order
            ? row.purchase_order.split(',').map((value) => {
                const trimmed = value.trim();
                return `<a href="/app/purchase-order/${encodeURIComponent(trimmed)}">${escape_html(trimmed)}</a>`;
              }).join(', ')
            : '<span class="text-muted">-</span>';
        const purchaseInvoice = row.purchase_invoice
            ? row.purchase_invoice.split(',').map((value) => {
                const trimmed = value.trim();
                return `<a href="/app/purchase-invoice/${encodeURIComponent(trimmed)}">${escape_html(trimmed)}</a>`;
              }).join(', ')
            : '<span class="text-muted">-</span>';

        return `
            <tr>
                <td style="padding: 12px; vertical-align: middle;">${escape_html(row.supplier_name)}</td>
                <td style="padding: 12px; vertical-align: middle;">${purchaseOrder}</td>
                <td style="padding: 12px; vertical-align: middle;">${purchaseInvoice}</td>
                <td class="text-right" style="padding: 12px; vertical-align: middle;">${format_currency(row.amount_tzs || 0, currency)}</td>
                <td style="padding: 12px; vertical-align: middle;">${escape_html(row.status_label)}</td>
            </tr>
        `;
    }).join('');

    wrapper.html(`
        <div class="table-responsive">
            <table class="table table-bordered" style="margin-bottom: 0; font-size: 14px;">
                <thead style="background-color: #f8f9fa;">
                    <tr>
                        <th style="padding: 12px; font-weight: 600; width: 20%;">Supplier</th>
                        <th style="padding: 12px; font-weight: 600; width: 20%;">Purchase Order</th>
                        <th style="padding: 12px; font-weight: 600; width: 25%;">Purchase Invoice</th>
                        <th class="text-right" style="padding: 12px; font-weight: 600; width: 15%;">Amount</th>
                        <th style="padding: 12px; font-weight: 600; width: 20%;">Status</th>
                    </tr>
                </thead>
                <tbody>${body}</tbody>
            </table>
        </div>
    `);
}
