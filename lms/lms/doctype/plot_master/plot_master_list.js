frappe.listview_settings['Plot Master'] = {

	add_fields: ['status', 'plot_number', 'plot_type'],

	get_indicator: function(doc) {
		if (doc.docstatus === 0) {
			return ['Draft', 'gray', 'docstatus,=,0'];
		}
		if (doc.docstatus === 2) {
			return ['Cancelled', 'red', 'docstatus,=,2'];
		}

		const map = {
			'Available': ['Available', 'green', 'status,=,Available'],
			'Pending Advance': ['Pending Advance', 'yellow', 'status,=,Pending Advance'],
			'Reserved': ['Reserved', 'orange', 'status,=,Reserved'],
			'Ready for Handover': ['Ready for Handover', 'blue', 'status,=,Ready for Handover'],
			'Delivered': ['Delivered', 'blue', 'status,=,Delivered'],
			'Title Closed': ['Title Closed', 'purple', 'status,=,Title Closed']
		};

		return map[doc.status] || ['Available', 'green', 'status,=,Available'];
	}

};
