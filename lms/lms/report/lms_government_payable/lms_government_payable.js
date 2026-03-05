frappe.query_reports["LMS Government Payable"] = {
	filters: [
		{
			fieldname: "status",
			label: "Fee Status",
			fieldtype: "Select",
			options: "All\nPosted\nPending",
			default: "All"
		},
		{
			fieldname: "from_date",
			label: "From Date",
			fieldtype: "Date"
		},
		{
			fieldname: "to_date",
			label: "To Date",
			fieldtype: "Date",
			default: frappe.datetime.get_today()
		}
	]
};
