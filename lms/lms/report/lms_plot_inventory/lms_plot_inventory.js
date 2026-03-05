frappe.query_reports["LMS Plot Inventory"] = {
	filters: [
		{
			fieldname: "land_acquisition",
			label: "Land Acquisition",
			fieldtype: "Link",
			options: "Land Acquisition"
		},
		{
			fieldname: "plot_type",
			label: "Plot Type",
			fieldtype: "Select",
			options: "\nResidential\nCommercial\nMixed-Use"
		},
		{
			fieldname: "status",
			label: "Status",
			fieldtype: "Select",
			options: "\nAvailable\nReserved\nDelivered\nTitle Closed"
		}
	]
};
