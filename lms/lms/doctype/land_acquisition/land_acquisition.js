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
        }
    },

    exchange_rate: function(frm) {
        calculate_tzs_cost(frm);
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
