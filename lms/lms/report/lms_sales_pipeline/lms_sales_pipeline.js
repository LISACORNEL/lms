frappe.query_reports["LMS Sales Pipeline"] = {
	filters: [
		{
			fieldname: "contract_status",
			label: "Status",
			fieldtype: "Select",
			options: "\nOngoing\nCompleted"
		},
		{
			fieldname: "customer",
			label: "Customer",
			fieldtype: "Link",
			options: "Customer"
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
