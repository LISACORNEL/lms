frappe.query_reports["LMS Business Trend"] = {
	filters: [
		{
			fieldname: "grouping",
			label: "Grouping",
			fieldtype: "Select",
			options: "Monthly\nWeekly",
			default: "Monthly",
			reqd: 1
		},
		{
			fieldname: "from_date",
			label: "From Date",
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -12),
			reqd: 1
		},
		{
			fieldname: "to_date",
			label: "To Date",
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1
		}
	]
};
