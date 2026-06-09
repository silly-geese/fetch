<!-- Phase 2 research spike for the fetch_einvoices engine. Decision record produced
2026-06-08 from a multi-source, adversarially-verified web research workflow. -->

# Phase 2 Decision Report: `fetch_einvoices(period, vendor?)` for pulling RECEIVED invoices for reconciliation

> **Status update (2026-06-08):** v1 ships the simpler **fetch-and-return** loop -
> fetch the accountant's missing invoices from the Gmail inbox plus a host-agent
> retrieval handoff (`plan_retrieval`), then **draft a reply** to the accountant
> with the files attached. The accounting-system *pull* integrations recommended
> below are **deferred**: this report is kept as the reference for the later
> "additional fetch source" adapters (Envoice / RIK / Peppol), not the v1 path.
> We do **not** write back into accounting software.

**Question being answered:** For each candidate Estonian system, can a 3rd-party app (running locally under the user's own keys) programmatically **list the invoices a company RECEIVED** (purchase / incoming e-invoices) for a date range, via a **documented API**? Sending is out of scope.

**Bottom line:** Yes, multiple systems support this. The cleanest wins are **accounting systems** that expose a `/purchase_invoices` list-by-date-range endpoint, because they return *what landed in the books regardless of channel* (manual, e-invoice operator, or Peppol), which is exactly the accountant's "what did we actually receive" source of truth. Operators/access points (Finbite, Maventa) also work but are SOAP/changed-since/queue-shaped and each one only sees *its own* slice of received invoices.

---

## 1. Ranking

| # | System | Pull RECEIVED for a period? | Auth (local-friendly?) | Reconciliation fit | Confidence |
|---|--------|------------------------------|-------------------------|--------------------|------------|
| 1 | **e-Financials / e-arveldaja (RIK)** | **Yes**: `GET /v1/purchase_invoices` with `start_date`/`end_date`/`modified_since`, paginated | Self-issued per-company API key (HMAC-SHA-384, IP-scoped, function-scoped). **Yes** | High | High |
| 2 | **Merit Aktiva** | **Yes**: `POST /getpurchorders` with `PeriodStart`/`PeriodEnd` (≤3-month windows) | Self-issued per-company Api ID + Api Key (HMAC-SHA-256 signed). **Yes** | High | High |
| 3 | **SimplBooks** | **Yes**: `GET /{company_id}/api/purchases/list` with `created_from`/`created_until`, paginated | Self-issued token `X-Simplbooks-Token` (Premium plan + API user). **Yes** | High | High |
| 4 | **Finbite** (ex-Omniva Arvekeskus) | **Yes**: SOAP `BuyInvoiceExport(since, state=RECEIVED)` | Self-issued per-company `authPhrase`, but needs an active Finbite contract. **Mostly** | High | High |
| 5 | **Unifiedpost / Fitek (FitekIN) Invoices Export v3** | **Yes**: `POST /ExportApi/Invoices.v3` with `InvoiceDate*` filters | `IntegratorId` + per-company `AuthorizationToken`, issued via BackOffice (not self-serve). **Partial** | High | High |
| 6 | **Peppol via Maventa (Visma AutoInvoice)** | **Yes**: `GET /v1/invoices?direction=RECEIVED&receivedAtStart/End` + `GET /v1/invoices/{id}` | OAuth2 client-credentials (company UUID + user API key + vendor_api_key). **Yes** | High | High |
| 7 | **Envoice (envoice.eu)** | **Endpoint exists** (`GET /partner/v1/invoices/purchases`), date-range filtering unconfirmed | OAuth2 bearer / account API token (grant unpinned). **Likely** | High (endpoint) / Low (date params) | Medium |
| 8 | **Telema** | **No**: receive is a consume-once **queue** (`GET /api/v1/data` + ack); no date filter, no history | clientId/clientSecret → Bearer; needs a Telema-configured channel. **Yes (auth)** | **Low** | High |
| - | Peppol network *itself* | **No**: no central inbox; you can only pull from your AP (see §3) | n/a | n/a | High |
| - | EE e-invoicing legal context | **No** (background, not an API) | n/a | n/a | High |

---

## 2. What to integrate FIRST

Build the provider interface (§5) and ship **two adapters first**:

### Pick #1: **e-Financials / e-arveldaja (RIK)**
- **State-owned, free, widely used** by Estonian micro-OÜs, the most likely system a small OÜ already has.
- **REST + JSON + OpenAPI 3.1** self-served at `https://rmp-api.rik.ee/openapi.yaml`, no SOAP, no XML wrangling.
- **First-class date-range list:** `GET /v1/purchase_invoices?start_date=…&end_date=…` (paginated), plus `clients_id` (maps to the optional `vendor?` arg) and `modified_since` (incremental sync).
- **Auth fits the toolkit perfectly:** each OÜ self-generates its own API key in the UI; HMAC-SHA-384 signing done locally; no central credential store.
- **Captures Peppol + manual:** RIK is a Peppol *receiving* AP and auto-creates received e-invoices as purchase invoices, so one sweep returns both Peppol-received and hand-entered invoices.

### Pick #2: **Merit Aktiva**
- Very common among Estonian OÜs and accountants.
- Two-step pull: `getpurchorders` (list by `PeriodStart`/`PeriodEnd`) → `getpurchorder` (detail + Lines/Payments + base64 attachment for the original PDF/XML).
- **Self-issued per-company Api ID + Api Key**, HMAC-SHA-256 request signing done locally, same local-credentials shape as RIK.
- Returns the *ledger* view: every purchase invoice recorded regardless of arrival channel, the correct "what we actually received" basis for an accountant diff.

**Why these two and not the operators first:** both are REST/JSON, both use a true `from/to` date-range filter, both use a self-issued per-company key with no central broker, and both return the ledger-level superset of received invoices. Finbite (SOAP, `since`-bookmark, contract-gated) and Fitek (XML, partner-onboarded credentials) are higher-friction; add them after the interface is proven. **SimplBooks** is an easy third adapter (REST/JSON, `X-Simplbooks-Token`) for OÜs on that platform.

**Watch-outs to bake in from day one:**
- **Date-field semantics:** RIK `start_date`/`end_date` and SimplBooks `created_*` filter on *record-creation date*, not invoice/turnover date; Merit's `DateType` lets you pick `DocumentDate` vs `ChangedDate`. Over-fetch a wider window and **post-filter client-side on the invoice/turnover date**.
- **No native vendor filter** on Merit (or the operators); apply `vendor?` client-side. (RIK has `clients_id`; SimplBooks has `client_id`/`client_name`.)
- **Chunking/pagination:** Merit caps each call at ~3 months; RIK/SimplBooks paginate, loop until exhausted.

---

## 3. The Peppol reality for receiving

**You cannot pull "from the Peppol network."** Peppol is a 4-corner model; the **recipient's Access Point (C3) is the only party that holds the received documents**: there is no central Peppol inbox or network-level query API.

Consequences:
- "Peppol" is **not a single pullable source**; model it as **"whichever AP/operator the OÜ receives through."**
- The best EE-reachable AP with a clean documented pull is **Maventa (Visma AutoInvoice)**: `GET /v1/invoices?direction=RECEIVED&receivedAtStart=…&receivedAtEnd=…` then `GET /v1/invoices/{id}`. (Query params are camelCase.)
- **Coverage risk:** a single AP only returns invoices that arrived *through that operator*. Surface a `source_operator` per invoice and treat single-source pulls as incomplete.
- This is precisely why the **accounting-system layer (RIK/Merit) is the better primary target**: it aggregates everything that actually landed in the books across all channels.

---

## 4. EE e-invoicing legal/mandate context (brief)

- **B2G mandatory since 1 July 2019.**
- **"Buyer's choice" since 1 July 2025:** any entity registered in the Commercial Register as an *e-invoice recipient* can demand structured EN 16931 e-invoices. Legacy EVS 923:2014 still accepted.
- **Full B2B mandate ~2027 is a forecast, not enacted law.**
- **Operator coordination:** as of July 2025 EE operators committed to give every business a Peppol mailbox and interconnect via roaming.

**Implication:** structured received invoices increasingly flow through the operator network + Peppol, so the right pull layer is the company's **operator account or accounting system**: not the legal framework. But **micro-OÜs that haven't registered as e-invoice recipients still get many invoices as email-PDF**, so the existing Gmail/PDF path remains a necessary fallback for complete coverage.

---

## 5. Proposed pluggable `EInvoiceProvider` architecture

Today, `fetch_invoices` (the Gmail provider) produces `InvoiceRecord`s → `record_to_dict()` → fed into `reconcile()` / `build_report()`. A `fetch_einvoices` capability is simply **a new family of providers that emit the same `InvoiceRecordDict` shape** and plug into the **same reconcile→report pipeline**.

### 5.1 The interface

```python
# src/invoices/providers/base.py
from typing import Protocol
from src.invoices.models import InvoiceRecordDict

class EInvoiceProvider(Protocol):
    name: str  # "rik", "merit", "simplbooks", "finbite", "maventa", ...

    def fetch_einvoices(
        self,
        period: str,          # "YYYY-MM" (reuse reconcile._period_key normalization)
        vendor: str | None = None,
        company_slug: str | None = None,
    ) -> list[InvoiceRecordDict]: ...
```

Each adapter: translate `period` → date-range params (over-fetch when creation-date-based); page/chunk; client-side filter to the true invoice date + `vendor`; map provider fields → `InvoiceRecordDict`, stamping `doc_type='invoice'` and a source marker.

### 5.2 Pipeline fit

```
                 ┌─ Gmail provider (existing fetch_invoices) ─┐
period, vendor ──┤  RIK / Merit / SimplBooks / Finbite / ...  ├─► list[InvoiceRecordDict]
                 └────────────────────────────────────────────┘
                                  │
                 (optional) classify_pdf for source PDFs/XML  ← reuse classify.py
                                  │
        reconcile(checklist_items, collected_records)  ← unchanged, deterministic
                                  │
                 build_report(...) → REPORT.md          ← unchanged
```

E-invoice records arrive **already structured**, so most **skip the `claude` classification step**; the filing (Dropbox) and reconcile/report steps are entirely reused. `reconcile()` already scores on invoice number, amount, period, and vendor tokens, exactly the fields e-invoice APIs return, so no matching changes are needed.

### 5.3 Field mapping (provider → `InvoiceRecordDict`)

| `InvoiceRecordDict` field | RIK | Merit | SimplBooks | Maventa |
|---|---|---|---|---|
| `invoice_number` | `number` | `BillNo` | `number` | invoice number |
| `beneficiary_name` / `sender` | `client_name` | `VendorName` | `client_name` | sender/supplier |
| `amount_inc_vat` | `gross_price` | `TotalAmount` | `total_sum` | total |
| `amount_ex_vat` | `net_price` | net | `sum` | net |
| `currency_code` | currency | `CurrencyCode` | `currency_name` | currency |
| `doc_date` (→ `_period_key`) | `journal_date` | `DocumentDate` | `transaction_date` | invoice date |
| `renamed_pdf` | from file id | from attachment | from `get_xml` | from `{id}` download |
| `doc_type` | `'invoice'` | `'invoice'` | `'invoice'` | `'invoice'` |

### 5.4 Credentials stay local

Follow the established pattern (`config.py` reads `config.yml` + `FETCH_*` env overrides; the server holds no credentials):

- **Per-company provider config in `config.yml`** under each slug (provider name + non-secret settings):
  ```yaml
  einvoice_providers:
    my-company:
      provider: rik          # rik | merit | simplbooks | finbite | maventa
      base_url: https://rmp-api.rik.ee
  ```
- **Secrets via env vars**, namespaced per provider/company (e.g. `FETCH_RIK_API_KEY_PUBLIC`/`…_PASSWORD`, `FETCH_MERIT_API_ID`/`…_API_KEY`, `FETCH_SIMPLBOOKS_TOKEN`).
- **Signing happens locally** in each adapter (RIK HMAC-SHA-384, Merit HMAC-SHA-256, Maventa OAuth2), each OÜ runs under its own self-issued keys.
- **New MCP tool `fetch_einvoices(period, vendor?, company?)`** returns `{count, invoices: [InvoiceRecordDict]}`, interchangeable with `fetch_invoices`/`list_invoices`, pipe straight into `reconcile`/`build_report`.

---

## 6. Open questions for the maintainer

1. **Which accounting system do your OÜs actually use?** (RIK e-arveldaja / Merit Aktiva / SimplBooks / other?)
2. **Which e-invoice operator(s) do they receive through?** (Finbite/ex-Omniva, Unifiedpost/Fitek, Telema, Maventa, Envoice, or mostly email-PDF?)
3. **Can each OÜ self-issue API keys today?** (RIK key / Merit Api ID+Key / SimplBooks token, needs Premium + API user.)
4. **On a Finbite/Fitek contract?** Can they get the per-company `authPhrase` (Finbite) or BackOffice `IntegratorId`+`AuthorizationToken` (Fitek), or does that need the accountant?
5. **Period diff basis:** invoice/turnover date or record-creation date?
6. **Coverage:** single authoritative source (favor the accounting system) or multi-source aggregation across operators + email-PDF? Is partial coverage OK for v1?
7. **Original document:** pull the PDF/XML per invoice for archiving, or are structured header fields enough for the diff?
8. **Single-OÜ or multi-OÜ deployments?**

---

## Sources (key URLs)

**e-Financials / e-arveldaja (RIK)**
- https://rmp-api.rik.ee/openapi.yaml (OpenAPI 3.1, `GET /v1/purchase_invoices`)
- https://abiinfo.rik.ee/en/e-financials/e-financials-api/technical-documentation-developers
- https://abiinfo.rik.ee/en/e-financials/e-financials-api/e-financials-api-key-generation

**Merit Aktiva**
- https://api.merit.ee/connecting-robots/reference-manual/purchase-invoices/get-list-of-purchase-invoices/
- https://api.merit.ee/connecting-robots/reference-manual/purchase-invoices/get-purchase-invoice-details/
- https://api.merit.ee/connecting-robots/reference-manual/authentication/

**SimplBooks**
- https://app.simplbooks.com/api-documentation/oas/paths/purchases_list.yaml
- https://support.simplbooks.ee/en/kasutusjuhendid/what-is-api-and-how-it-works/

**Finbite (ex-Omniva Arvekeskus)**
- https://app.finbite.eu/finance/erp/erpServices.wsdl (`BuyInvoiceExport`)
- https://help.finbite.eu/en/articles/8717039-finbite-environment-url-links

**Unifiedpost / Fitek (FitekIN)**
- https://fitekgroup.atlassian.net/wiki/spaces/FAD/pages/2127724583/Export+API+Invoices+Export+v3
- https://fitekin.com/ExportApi/swagger

**Peppol / Maventa (Visma AutoInvoice)**
- https://documentation.maventa.com/integration-guide/invoice-receiving/invoice-receiving-guide/
- https://swagger.maventa.com/

**Envoice / Telema**
- https://envoice.eu/en/integrations/
- https://telema.com/technical-guidelines-for-implementing/

**Legal / context**
- https://ec.europa.eu/digital-building-blocks/sites/spaces/DIGITAL/pages/467108883/eInvoicing+in+Estonia
- https://sovos.com/regulatory-updates/vat/estonia-mandatory-b2b-e-invoicing-upon-buyers-request-approved/
