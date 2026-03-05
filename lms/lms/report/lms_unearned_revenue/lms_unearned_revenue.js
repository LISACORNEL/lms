frappe.query_reports["LMS Unearned Revenue"] = {
	filters: [
		{
			fieldname: "customer",
			label: "Customer",
			fieldtype: "Link",
			options: "Customer"
		}
	]
};
