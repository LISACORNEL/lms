# LMS — Land Management System

A custom Frappe app built on ERPNext v15 for managing the full lifecycle of land acquisition, plot subdivision, customer contracts, and installment payments for a Tanzanian land development company.

---

## Overview

LMS handles the end-to-end process:

```
Land Acquisition → Plot Subdivision → Customer Contract → Installment Payments → Delivery → Title Closure
```

Key business rules:
- All payments post to **Customer Advances** (liability) and are recognized as revenue only at delivery + title closure (deferred revenue model)
- A configurable **booking fee** (default 60%) is required upfront to reserve a plot
- The remaining balance is split into **monthly installments** over a configurable window (default 90 days)
- A configurable **government share** percentage is withheld from every payment automatically
- Forfeited deposits (cancelled contracts) are posted to a dedicated income account — no refunds

---

## Modules

### LMS Settings *(Single)*
Central configuration for the app. Stores GL account mappings, warehouse, booking fee percentage, payment window, and naming series for all documents.

### Government Fee Schedule *(Single)*
Stores the current government share percentage applied to every customer payment.

### Land Acquisition
Records the purchase of raw land from a seller. Includes cost breakdown, area, legal status, and seller information.

**Workflow:**
1. Create and save → calculates TZS cost from currency + exchange rate
2. Submit → status moves to **Pending Approval**
3. Click **Approve** → posts Journal Entry: `Dr 1705 Land Under Development / Cr 1201 Bank`; status moves to **Approved**

### Plot Master
Represents one individual plot carved out of an approved Land Acquisition.

**Workflow:**
1. Create → select an Approved Land Acquisition; allocated cost is auto-calculated (`acquisition cost ÷ total sqm × plot sqm`)
2. Submit → creates a **Stock Entry (Material Receipt)** bringing the plot into Plot Inventory Warehouse at allocated cost

**Statuses:** Available → Reserved → Delivered → Title Closed

### Plot Contract
The sale agreement between the company and a customer for a specific plot.

**Workflow:**
1. Create → select a Customer and an Available plot; selling price, booking fee, and payment schedule auto-fill
2. Save → payment schedule is generated with monthly installments
3. Submit → plot status changes to **Reserved**; contract status changes to **Active**
4. Cancel → plot returns to **Available**; contract status changes to **Cancelled**

---

## Requirements

- Frappe Framework v15
- ERPNext v15
- Python ≥ 3.10

---

## Installation

```bash
cd /path/to/frappe-bench
bench get-app https://github.com/LISACORNEL/lms.git
bench install-app lms
bench migrate
bench restart
```

---

## Configuration

After installation, go to **LMS Settings** and configure:

| Field | Description |
|-------|-------------|
| Company | The ERPNext company to post all transactions against |
| Default Currency | TZS (or your base currency) |
| Land Under Development Account | Dr account for land purchases |
| Plot Inventory Account | Stock asset account for plots |
| Customer Advances Account | Liability account for all incoming payments |
| Plot Sales Revenue Account | Income account recognized at title closure |
| Cost of Land Sold Account | COGS account recognized at title closure |
| Any Bank Account | Bank account for  integration |
| Government Payable Account | Liability account for withheld government fees |
| Forfeited Deposits Income Account | Income account for cancelled contract deposits |
| Plot Inventory Warehouse | Warehouse where plots are tracked as stock |
| Booking Fee Percentage | % of selling price required at booking (default 60%) |
| Payment Completion Days | Days customer has to complete payment (default 90) |

Also configure **Government Fee Schedule** with the current government share percentage.

---

## Chart of Accounts

The following GL accounts are required under your company:

| Code | Name | Type |
|------|------|------|
| 1050 | Plot Inventory | Stock (Current Asset) |
| 1201 | Main Operating Bank Account | Bank |
| 1205 | TCB Bank Account | Bank |
| 1705 | Land Under Development | Fixed Asset |
| 2105 | Customer Advances (Unearned Plot Sales) | Payable |
| 2108 | Government Payable | Payable |
| 4101 | Plot Sales Revenue | Income |
| 4203 | Forfeited Deposits Income | Income |
| 5101 | Cost of Land Sold | COGS |

---

## License

MIT
