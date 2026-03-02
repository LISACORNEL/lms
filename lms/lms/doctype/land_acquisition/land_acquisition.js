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
        if (frm.doc.status === "Approved") {
            frm.dashboard.set_headline_alert(
                'This Land Acquisition is Approved',
                'green'
            );
        }
    },

    // Auto calculate TZS cost when cost or exchange rate changes
    total_acquisition_cost: function(frm) {
        calculate_tzs_cost(frm);
    },
    exchange_rate: function(frm) {
        calculate_tzs_cost(frm);
    }
});

function calculate_tzs_cost(frm) {
    let cost = flt(frm.doc.total_acquisition_cost);
    let rate = flt(frm.doc.exchange_rate) || 1;
    frm.set_value('acquisition_cost_tzs', cost * rate);
}
