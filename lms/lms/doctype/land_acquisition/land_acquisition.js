frappe.ui.form.on('Land Acquisition', {
    refresh: function(frm) {
        // Show Approve button only when submitted and pending approval
        if (frm.doc.docstatus === 1 && frm.doc.status === "Pending Approval") {
            frm.add_custom_button('Approve', function() {
                frappe.confirm(
                    'Are you sure you want to approve this Land Acquisition?',
                    function() {
                        frappe.call({
                            method: 'approve',
                            doc: frm.doc,
                            callback: function(r) {
                                frm.reload_doc();
                            }
                        });
                    }
                );
            }).addClass('btn-primary');
        }

        // Show status as a color indicator
        if (frm.doc.docstatus === 1 && frm.doc.status === "Approved") {
            frm.dashboard.set_headline_alert(
                'This Land Acquisition is Approved',
                'green'
            );
        }

        // Populate plot count summary (only for saved docs)
        if (frm.doc.name && !frm.doc.__islocal) {
            refresh_plot_counts(frm);
        }
    },

    exchange_rate: function(frm) {
        calculate_tzs_cost(frm);
    }
});

function refresh_plot_counts(frm) {
    const statuses = ['Available', 'Reserved', 'Delivered', 'Title Closed'];
    const counts = { total: 0, available: 0, reserved: 0, delivered: 0 };

    let done = 0;
    statuses.forEach(function(status) {
        frappe.call({
            method: 'frappe.client.get_count',
            args: {
                doctype: 'Plot Master',
                filters: { land_acquisition: frm.doc.name, status: status }
            },
            callback: function(r) {
                const n = r.message || 0;
                counts.total += n;
                if (status === 'Available') counts.available = n;
                if (status === 'Reserved') counts.reserved = n;
                if (status === 'Delivered' || status === 'Title Closed') counts.delivered += n;
                done++;
                if (done === statuses.length) {
                    frm.doc.total_plots = counts.total;
                    frm.doc.available_plots = counts.available;
                    frm.doc.reserved_plots = counts.reserved;
                    frm.doc.delivered_plots = counts.delivered;
                    frm.refresh_field('total_plots');
                    frm.refresh_field('available_plots');
                    frm.refresh_field('reserved_plots');
                    frm.refresh_field('delivered_plots');
                }
            }
        });
    });
}

// Trigger when any cost item row amount changes or a row is removed
frappe.ui.form.on('Land Acquisition Cost Item', {
    amount: function(frm) {
        recalculate_total(frm);
    },
    cost_items_remove: function(frm) {
        recalculate_total(frm);
    }
});

function recalculate_total(frm) {
    let total = 0;
    (frm.doc.cost_items || []).forEach(row => {
        total += flt(row.amount);
    });
    frm.set_value('total_acquisition_cost', total);
    calculate_tzs_cost(frm);
}

function calculate_tzs_cost(frm) {
    let cost = flt(frm.doc.total_acquisition_cost);
    let rate = flt(frm.doc.exchange_rate) || 1;
    frm.set_value('acquisition_cost_tzs', cost * rate);
}
