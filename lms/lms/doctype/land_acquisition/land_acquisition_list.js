frappe.listview_settings['Land Acquisition'] = {

	add_fields: ['acquisition_name', 'status', 'acquisition_date', 'region', 'acquisition_cost_tzs'],

	get_indicator: function(doc) {
		const map = {
			'Draft':            ['Draft',            'gray',   'status,=,Draft'],
			'Pending Approval': ['Pending Approval', 'orange', 'status,=,Pending Approval'],
			'Approved':         ['Approved',         'green',  'status,=,Approved'],
			'Subdivided':       ['Subdivided',       'blue',   'status,=,Subdivided']
		};
		const entry = map[doc.status] || ['Draft', 'gray', 'status,=,Draft'];
		return entry;
	}

};
