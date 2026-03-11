import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt


class TCBIntegrationSettings(Document):

    def validate(self):
        self._normalize_modes()
        self._normalize_decline_policy()
        self._validate_timeouts()
        self._validate_reconciliation_config()
        self._validate_required_fields_for_live_modes()

    def _normalize_modes(self):
        allowed_outbound = {"Off", "Log Only", "Live"}
        allowed_inbound = {"Off", "Log Only", "Apply Payment"}

        self.outbound_mode = (self.outbound_mode or "Off").strip()
        self.inbound_mode = (self.inbound_mode or "Off").strip()

        if self.outbound_mode not in allowed_outbound:
            frappe.throw(f"Invalid Outbound Mode: {self.outbound_mode}")

        if self.inbound_mode not in allowed_inbound:
            frappe.throw(f"Invalid Inbound Mode: {self.inbound_mode}")

    def _validate_timeouts(self):
        connect_timeout = flt(self.connect_timeout_seconds or 0)
        read_timeout = flt(self.read_timeout_seconds or 0)

        if connect_timeout <= 0:
            frappe.throw("Connect Timeout (seconds) must be greater than zero.")

        if read_timeout <= 0:
            frappe.throw("Read Timeout (seconds) must be greater than zero.")

    def _normalize_decline_policy(self):
        allowed = {"Allow Cancel and Flag", "Block Cancel"}
        self.decline_failure_policy = (self.decline_failure_policy or "Allow Cancel and Flag").strip()
        if self.decline_failure_policy not in allowed:
            frappe.throw(f"Invalid Decline Failure Policy: {self.decline_failure_policy}")

    def _validate_reconciliation_config(self):
        if cint(self.reconciliation_enabled):
            lookback = cint(self.reconciliation_lookback_days or 0)
            if lookback <= 0:
                frappe.throw("Reconciliation Lookback (Days) must be greater than zero.")

    def _validate_required_fields_for_live_modes(self):
        if not cint(self.enabled):
            return

        if self.outbound_mode == "Live":
            missing = []
            if not self.api_key:
                missing.append("API Key")
            if not self.partner_code:
                missing.append("Partner Code")
            if not self.profile_id:
                missing.append("Profile ID / Account Number")
            if missing:
                frappe.throw(
                    "TCB outbound mode is Live but required fields are missing: "
                    + ", ".join(missing)
                )

        if self.inbound_mode in ("Log Only", "Apply Payment") and not self.callback_token:
            frappe.throw(
                "Callback Token is required when Inbound Mode is Log Only or Apply Payment."
            )
