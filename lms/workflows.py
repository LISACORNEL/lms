import frappe


LAND_ACQUISITION_APPROVER_ROLE = "Land Acquisition Approver"
LAND_ACQUISITION_WORKFLOW = "Land Acquisition Approval"


def ensure_lms_workflows():
	ensure_role(LAND_ACQUISITION_APPROVER_ROLE)
	ensure_workflow_action("Submit for Approval")
	ensure_workflow_action("Send Back")
	ensure_workflow_action("Approve")
	ensure_workflow_state("Draft", style="Inverse")
	ensure_workflow_state("Pending Approval", style="Warning")
	ensure_workflow_state("Approved", style="Success")
	ensure_land_acquisition_workflow()
	frappe.clear_cache()
	frappe.db.commit()


def ensure_role(role_name):
	if frappe.db.exists("Role", role_name):
		return

	frappe.get_doc(
		{
			"doctype": "Role",
			"role_name": role_name,
		}
	).insert(ignore_permissions=True)


def ensure_workflow_action(action_name):
	if frappe.db.exists("Workflow Action Master", action_name):
		return

	frappe.get_doc(
		{
			"doctype": "Workflow Action Master",
			"workflow_action_name": action_name,
		}
	).insert(ignore_permissions=True)


def ensure_workflow_state(state_name, style=None):
	if frappe.db.exists("Workflow State", state_name):
		return

	doc = frappe.get_doc(
		{
			"doctype": "Workflow State",
			"workflow_state_name": state_name,
			"style": style,
		}
	)
	doc.insert(ignore_permissions=True)


def ensure_land_acquisition_workflow():
	for workflow_name in frappe.get_all(
		"Workflow",
		filters={"document_type": "Land Acquisition"},
		pluck="name",
	):
		if workflow_name != LAND_ACQUISITION_WORKFLOW:
			frappe.db.set_value("Workflow", workflow_name, "is_active", 0, update_modified=False)

	if frappe.db.exists("Workflow", LAND_ACQUISITION_WORKFLOW):
		workflow = frappe.get_doc("Workflow", LAND_ACQUISITION_WORKFLOW)
	else:
		workflow = frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": LAND_ACQUISITION_WORKFLOW,
				"document_type": "Land Acquisition",
			}
		)

	workflow.document_type = "Land Acquisition"
	workflow.workflow_state_field = "approval_state"
	workflow.is_active = 1
	workflow.send_email_alert = 0
	workflow.override_status = 1
	workflow.set("states", [])
	workflow.set("transitions", [])

	workflow.append(
		"states",
		{
			"state": "Draft",
			"doc_status": "0",
			"allow_edit": "Sales",
			"update_field": "status",
			"update_value": "Draft",
		},
	)
	workflow.append(
		"states",
		{
			"state": "Pending Approval",
			"doc_status": "0",
			"allow_edit": LAND_ACQUISITION_APPROVER_ROLE,
			"update_field": "status",
			"update_value": "Pending Approval",
		},
	)
	workflow.append(
		"states",
		{
			"state": "Approved",
			"doc_status": "1",
			"allow_edit": "System Manager",
			"update_field": "status",
			"update_value": "Approved",
		},
	)

	workflow.append(
		"transitions",
		{
			"state": "Draft",
			"action": "Submit for Approval",
			"next_state": "Pending Approval",
			"allowed": "Sales",
			"allow_self_approval": 1,
		},
	)
	workflow.append(
		"transitions",
		{
			"state": "Pending Approval",
			"action": "Send Back",
			"next_state": "Draft",
			"allowed": LAND_ACQUISITION_APPROVER_ROLE,
			"allow_self_approval": 1,
		},
	)
	workflow.append(
		"transitions",
		{
			"state": "Pending Approval",
			"action": "Approve",
			"next_state": "Approved",
			"allowed": LAND_ACQUISITION_APPROVER_ROLE,
			"allow_self_approval": 0,
		},
	)

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)
