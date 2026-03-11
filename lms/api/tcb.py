import json

import frappe
from frappe.utils import cstr

from lms.lms.tcb import (
    apply_tcb_payment_to_sales_order,
    create_tcb_api_log,
    get_tcb_inbound_mode,
    has_duplicate_ipn,
    is_callback_auto_apply_enabled,
    validate_callback_token,
)


@frappe.whitelist(allow_guest=True)
def receive_ipn(**kwargs):
    """TCB Instant Payment Notification callback endpoint.

    Behavior is controlled by TCB Integration Settings:
      - validates callback token
      - logs payload and duplicate callbacks
      - supports Off, Log Only, and Apply Payment processing modes
    """
    payload, raw_body = _read_payload(kwargs)
    reference, transaction_id = _extract_reference_and_transaction(payload)
    so_name = _find_sales_order_by_reference(reference)
    provided_token = _extract_callback_token(kwargs)
    inbound_mode = get_tcb_inbound_mode()
    callback_status_code, callback_status_desc = _extract_callback_status(payload)

    if inbound_mode == "Off":
        create_tcb_api_log(
            direction="Inbound",
            event_type="IPN Callback",
            status="Ignored",
            processing_mode="Off",
            endpoint="/api/method/lms.api.tcb.receive_ipn",
            external_reference=reference,
            transaction_id=transaction_id,
            plot_sales_order=so_name,
            request_payload=payload or raw_body,
            response_payload={"Status": 0, "Message": "Inbound mode Off. Payload logged and ignored."},
        )
        return {"Status": 0, "Message": "Inbound mode Off. Payload logged and ignored."}

    if not validate_callback_token(provided_token):
        create_tcb_api_log(
            direction="Inbound",
            event_type="IPN Callback",
            status="Failed",
            processing_mode=inbound_mode,
            endpoint="/api/method/lms.api.tcb.receive_ipn",
            external_reference=reference,
            transaction_id=transaction_id,
            plot_sales_order=so_name,
            request_payload=payload or raw_body,
            response_payload={"Status": 1, "Message": "Unauthorized callback token."},
            error="Token validation failed.",
        )
        frappe.local.response["http_status_code"] = 401
        return {"Status": 1, "Message": "Unauthorized callback token."}

    is_duplicate = bool(transaction_id and reference and has_duplicate_ipn(transaction_id, reference))
    if is_duplicate:
        create_tcb_api_log(
            direction="Inbound",
            event_type="IPN Callback",
            status="Ignored",
            processing_mode=inbound_mode,
            endpoint="/api/method/lms.api.tcb.receive_ipn",
            external_reference=reference,
            transaction_id=transaction_id,
            plot_sales_order=so_name,
            is_duplicate=1,
            request_payload=payload or raw_body,
            response_payload={"Status": 0, "Message": "Duplicate callback ignored."},
        )
        return {"Status": 0, "Message": "Duplicate callback ignored."}

    if inbound_mode == "Apply Payment":
        if not is_callback_auto_apply_enabled():
            create_tcb_api_log(
                direction="Inbound",
                event_type="IPN Callback",
                status="Ignored",
                processing_mode="Apply Payment",
                endpoint="/api/method/lms.api.tcb.receive_ipn",
                external_reference=reference,
                transaction_id=transaction_id,
                plot_sales_order=so_name,
                request_payload=payload or raw_body,
                response_payload={"Status": 0, "Message": "Auto-apply callback switch is OFF."},
            )
            return {"Status": 0, "Message": "Apply mode is configured but auto-apply switch is OFF."}

        if callback_status_code not in (None, 0):
            create_tcb_api_log(
                direction="Inbound",
                event_type="IPN Callback",
                status="Failed",
                processing_mode="Apply Payment",
                endpoint="/api/method/lms.api.tcb.receive_ipn",
                tcb_status_code=callback_status_code,
                tcb_message=callback_status_desc,
                external_reference=reference,
                transaction_id=transaction_id,
                plot_sales_order=so_name,
                request_payload=payload or raw_body,
                response_payload={"Status": 1, "Message": "Callback status is not success; payment not applied."},
            )
            return {"Status": 1, "Message": "Callback status is not success; payment not applied."}

        amount, payment_date = _extract_amount_and_date(payload)
        apply_result = apply_tcb_payment_to_sales_order(
            control_number=reference,
            amount=amount,
            payment_date=payment_date,
            payment_reference=transaction_id or reference,
        )
        create_tcb_api_log(
            direction="Inbound",
            event_type="IPN Callback",
            status=apply_result.get("status") or ("Success" if apply_result.get("ok") else "Failed"),
            processing_mode="Apply Payment",
            endpoint="/api/method/lms.api.tcb.receive_ipn",
            tcb_status_code=callback_status_code,
            tcb_message=callback_status_desc,
            external_reference=reference,
            transaction_id=transaction_id,
            plot_sales_order=apply_result.get("plot_sales_order") or so_name,
            payment_entry=apply_result.get("payment_entry"),
            request_payload=payload or raw_body,
            response_payload={"message": apply_result.get("message")},
            error=apply_result.get("error"),
        )
        if apply_result.get("ok"):
            return {"Status": 0, "Message": apply_result.get("message") or "Payment auto-applied."}
        return {"Status": 1, "Message": apply_result.get("message") or "Payment auto-apply failed."}

    # Log Only mode: acknowledge callback and log without posting payment entries.
    create_tcb_api_log(
        direction="Inbound",
        event_type="IPN Callback",
        status="Success" if callback_status_code in (None, 0) else "Failed",
        processing_mode="Log Only",
        endpoint="/api/method/lms.api.tcb.receive_ipn",
        tcb_status_code=callback_status_code,
        tcb_message=callback_status_desc,
        external_reference=reference,
        transaction_id=transaction_id,
        plot_sales_order=so_name,
        request_payload=payload or raw_body,
        response_payload={"Status": 0, "Message": "IPN received and logged (Log Only mode)."},
    )

    return {"Status": 0, "Message": "IPN received and logged (Log Only mode)."}


def _read_payload(kwargs):
    raw_body = ""
    parsed = None

    try:
        if frappe.request:
            raw_body = frappe.request.get_data(as_text=True) or ""
    except Exception:
        raw_body = ""

    if raw_body.strip():
        try:
            parsed = json.loads(raw_body)
        except Exception:
            parsed = None

    if parsed is None and kwargs:
        parsed = kwargs

    return parsed or {}, raw_body


def _extract_callback_token(kwargs):
    token = cstr(kwargs.get("token") or "")
    if token:
        return token

    token = cstr(_safe_request_header("X-TCB-Token") or "")
    if token:
        return token

    auth = cstr(_safe_request_header("Authorization") or "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()

    return ""


def _extract_reference_and_transaction(payload):
    if not isinstance(payload, dict):
        return "", ""

    params = payload.get("param") if isinstance(payload.get("param"), dict) else payload
    reference = cstr(params.get("reference") or params.get("refNo") or "").strip()
    transaction_id = cstr(params.get("transaction_id") or params.get("transactionId") or "").strip()
    return reference, transaction_id


def _extract_callback_status(payload):
    if not isinstance(payload, dict):
        return None, ""
    status_code = payload.get("status")
    status_desc = cstr(payload.get("statusDesc") or payload.get("status_desc") or "").strip()
    try:
        status_code = int(status_code) if status_code is not None else None
    except Exception:
        status_code = None
    return status_code, status_desc


def _extract_amount_and_date(payload):
    if not isinstance(payload, dict):
        return 0, ""
    params = payload.get("param") if isinstance(payload.get("param"), dict) else payload
    amount = params.get("amount") or 0
    payment_date = params.get("transaction_date") or params.get("trans_date") or ""
    return amount, payment_date


def _find_sales_order_by_reference(reference):
    if not reference:
        return ""
    return (
        frappe.db.get_value(
            "Plot Sales Order",
            {"control_number": reference, "docstatus": 1},
            "name",
        )
        or ""
    )


def _safe_request_header(key):
    try:
        return frappe.get_request_header(key)
    except Exception:
        return ""
