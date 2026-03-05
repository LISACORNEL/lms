frappe.listview_settings['Plot Contract'] = {

	add_fields: ['contract_status', 'customer', 'plot', 'contract_date'],

	get_indicator: function(doc) {
		const map = {
			'Draft':      ['Draft',      'gray',   'contract_status,=,Draft'],
			'Ongoing':    ['Ongoing',    'yellow', 'contract_status,=,Ongoing'],
			'Completed':  ['Completed',  'green',  'contract_status,=,Completed'],
			'Cancelled':  ['Cancelled',  'red',    'contract_status,=,Cancelled'],
			'Terminated': ['Terminated', 'orange', 'contract_status,=,Terminated']
		};
		return map[doc.contract_status] || ['Draft', 'gray', 'contract_status,=,Draft'];
	}

};
