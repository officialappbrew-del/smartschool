"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, CreditCard, Plus, RefreshCw, Wallet } from "lucide-react";
import FeedbackModal from "./FeedbackModal";
import { schoolApi } from "../lib/school-api";

type BillingSettings = {
  provider: "manual" | "paystack" | "stripe";
  currency: string;
  public_key: string | null;
  has_secret_key: boolean;
  has_webhook_secret: boolean;
  configured: boolean;
};

type SchoolClass = {
  id: string;
  name: string;
  academic_year_id: string;
  academic_year: string;
};

type AcademicYear = { id: string; name: string };
type AcademicTerm = { id: string; academic_year_id: string; name: string };

type FeeStructure = {
  id: string;
  class_section_id: string;
  class_name: string;
  academic_year: string;
  term_id: string;
  term_name: string;
  description: string;
  amount: number | string;
  currency: string;
  is_active: boolean;
};

type Payment = {
  id: string;
  amount: number | string;
  method: string;
  reference: string | null;
  paid_at: string;
  voided_at: string | null;
};

type Invoice = {
  id: string;
  invoice_number: string;
  student_name: string;
  admission_number: string;
  class_name: string;
  term_name: string;
  amount: number | string;
  currency: string;
  balance: number | string;
  status: "issued" | "paid" | "overdue";
  issued_at: string;
  due_date: string;
  payments: Payment[];
};

type BillingCurrencySummary = {
  currency: string;
  total_invoiced: number | string;
  total_collected: number | string;
  total_outstanding: number | string;
  collection_rate: number | string;
  invoice_count: number;
  paid_invoice_count: number;
  overdue_invoice_count: number;
};

type BillingDefaulter = {
  invoice_id: string;
  invoice_number: string;
  student_id: string;
  student_name: string;
  admission_number: string;
  class_name: string;
  term_name: string;
  due_date: string;
  days_overdue: number;
  balance: number | string;
  currency: string;
};

type BillingDashboard = {
  summaries: BillingCurrencySummary[];
  defaulters: BillingDefaulter[];
  defaulters_total: number;
};

function money(amount: number | string, currency: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(amount));
}

export default function BillingWorkspace() {
  const [settings, setSettings] = useState<BillingSettings | null>(null);
  const [classes, setClasses] = useState<SchoolClass[]>([]);
  const [years, setYears] = useState<AcademicYear[]>([]);
  const [terms, setTerms] = useState<AcademicTerm[]>([]);
  const [fees, setFees] = useState<FeeStructure[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [dashboard, setDashboard] = useState<BillingDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [provider, setProvider] = useState<BillingSettings["provider"]>("manual");
  const [currency, setCurrency] = useState("NGN");
  const [publicKey, setPublicKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [feeClassId, setFeeClassId] = useState("");
  const [feeTermId, setFeeTermId] = useState("");
  const [feeDescription, setFeeDescription] = useState("");
  const [feeAmount, setFeeAmount] = useState("");
  const [invoiceClassId, setInvoiceClassId] = useState("");
  const [invoiceTermId, setInvoiceTermId] = useState("");
  const [invoiceDueDate, setInvoiceDueDate] = useState("");
  const [dashboardTermId, setDashboardTermId] = useState("");
  const [paymentInvoiceId, setPaymentInvoiceId] = useState("");
  const [paymentAmount, setPaymentAmount] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<"cash" | "bank_transfer">("bank_transfer");
  const [paymentReference, setPaymentReference] = useState("");
  const [paymentToVoid, setPaymentToVoid] = useState("");

  const feeTerms = useMemo(
    () => terms.filter((term) => term.academic_year_id === classes.find((item) => item.id === feeClassId)?.academic_year_id),
    [classes, feeClassId, terms],
  );
  const invoiceTerms = useMemo(
    () => terms.filter((term) => term.academic_year_id === classes.find((item) => item.id === invoiceClassId)?.academic_year_id),
    [classes, invoiceClassId, terms],
  );

  async function loadWorkspace(termId = "") {
    const dashboardQuery = new URLSearchParams({ defaulters_limit: "20" });
    if (termId) dashboardQuery.set("term_id", termId);
    const [currentSettings, classList, yearList, feeList, invoiceList, dashboardData] = await Promise.all([
      schoolApi<BillingSettings>("/api/v1/billing/settings"),
      schoolApi<SchoolClass[]>("/api/v1/classes"),
      schoolApi<AcademicYear[]>("/api/v1/academic-years"),
      schoolApi<FeeStructure[]>("/api/v1/billing/fees"),
      schoolApi<{ items: Invoice[]; total: number }>("/api/v1/billing/invoices"),
      schoolApi<BillingDashboard>(`/api/v1/billing/dashboard?${dashboardQuery}`),
    ]);
    const termLists = await Promise.all(
      yearList.map((year) =>
        schoolApi<AcademicTerm[]>(`/api/v1/academic-years/${encodeURIComponent(year.id)}/terms`),
      ),
    );
    setSettings(currentSettings);
    setProvider(currentSettings.provider);
    setCurrency(currentSettings.currency);
    setPublicKey(currentSettings.public_key ?? "");
    setClasses(classList);
    setYears(yearList);
    setTerms(termLists.flat());
    setFees(feeList);
    setInvoices(invoiceList.items);
    setDashboard(dashboardData);
    setFeeClassId((current) => current || classList[0]?.id || "");
    setInvoiceClassId((current) => current || classList[0]?.id || "");
  }

  async function loadDashboard(termId: string) {
    const query = new URLSearchParams({ defaulters_limit: "20" });
    if (termId) query.set("term_id", termId);
    try {
      setDashboard(await schoolApi<BillingDashboard>(`/api/v1/billing/dashboard?${query}`));
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to load the fee dashboard.");
    }
  }

  useEffect(() => {
    let active = true;
    loadWorkspace()
      .catch((error: unknown) => {
        if (active) setNotice(error instanceof Error ? error.message : "Unable to load billing.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function refresh() {
    setSaving(true);
    try {
      await loadWorkspace(dashboardTermId);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to refresh billing.");
    } finally {
      setSaving(false);
    }
  }

  async function saveSettings(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const updated = await schoolApi<BillingSettings>("/api/v1/billing/settings", {
        method: "PUT",
        body: JSON.stringify({
          provider,
          currency: currency.toUpperCase(),
          public_key: publicKey.trim() || null,
          ...(secretKey.trim() ? { secret_key: secretKey.trim() } : {}),
          ...(webhookSecret.trim() ? { webhook_secret: webhookSecret.trim() } : {}),
        }),
      });
      setSettings(updated);
      setSecretKey("");
      setWebhookSecret("");
      setNotice("Billing settings saved.");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to save billing settings.");
    } finally {
      setSaving(false);
    }
  }

  async function createFee(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      await schoolApi<FeeStructure>("/api/v1/billing/fees", {
        method: "POST",
        body: JSON.stringify({
          class_section_id: feeClassId,
          term_id: feeTermId,
          description: feeDescription,
          amount: feeAmount,
        }),
      });
      setFeeDescription("");
      setFeeAmount("");
      setNotice("Fee item added.");
      await loadWorkspace(dashboardTermId);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to create the fee item.");
    } finally {
      setSaving(false);
    }
  }

  async function toggleFee(fee: FeeStructure) {
    setSaving(true);
    try {
      await schoolApi<FeeStructure>(`/api/v1/billing/fees/${encodeURIComponent(fee.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: !fee.is_active }),
      });
      await loadWorkspace(dashboardTermId);
      setNotice(fee.is_active ? "Fee item deactivated." : "Fee item reactivated.");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to update the fee item.");
    } finally {
      setSaving(false);
    }
  }

  async function generateInvoices(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const result = await schoolApi<{ created: number; skipped: number }>(
        "/api/v1/billing/invoices/generate",
        {
          method: "POST",
          body: JSON.stringify({
            class_section_id: invoiceClassId,
            term_id: invoiceTermId,
            due_date: invoiceDueDate,
          }),
        },
      );
      await loadWorkspace(dashboardTermId);
      setNotice(`Generated ${result.created} invoice(s); ${result.skipped} already existed.`);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to generate invoices.");
    } finally {
      setSaving(false);
    }
  }

  async function recordPayment(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      await schoolApi<Invoice>(
        `/api/v1/billing/invoices/${encodeURIComponent(paymentInvoiceId)}/payments`,
        {
          method: "POST",
          body: JSON.stringify({
            amount: paymentAmount,
            method: paymentMethod,
            reference: paymentReference.trim() || null,
            paid_at: new Date().toISOString(),
          }),
        },
      );
      setPaymentInvoiceId("");
      setPaymentAmount("");
      setPaymentReference("");
      await loadWorkspace(dashboardTermId);
      setNotice("Payment recorded.");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to record payment.");
    } finally {
      setSaving(false);
    }
  }

  async function voidPayment(paymentId: string) {
    setSaving(true);
    try {
      await schoolApi<Invoice>(`/api/v1/billing/payments/${encodeURIComponent(paymentId)}/void`, {
        method: "POST",
        body: JSON.stringify({ reason: "Voided by school administrator" }),
      });
      setPaymentToVoid("");
      await loadWorkspace(dashboardTermId);
      setNotice("Payment voided; the audit record was retained.");
    } catch (error: unknown) {
      setPaymentToVoid("");
      setNotice(error instanceof Error ? error.message : "Unable to void the payment.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <div className="workspace-loading" aria-live="polite">Loading billing…</div>;
  }

  return (
    <div className="billing-workspace">
      <div className="billing-section-heading">
        <div>
          <p className="eyebrow">TENANT BILLING</p>
          <h2>Fees &amp; payments</h2>
          <p className="page-description">Configure a provider and currency, define term fees, and manage invoices.</p>
        </div>
        <button className="button button-secondary" type="button" onClick={() => void refresh()} disabled={saving}>
          <RefreshCw size={15} /> Refresh
        </button>
      </div>

      <section className="panel billing-card">
        <div className="billing-dashboard-heading">
          <div>
            <div className="panel-header">
              <div><h2>Fee dashboard</h2><p>Collection performance and overdue balances.</p></div>
            </div>
          </div>
          <label className="billing-term-filter">Term
            <select value={dashboardTermId} onChange={(event) => {
              const termId = event.target.value;
              setDashboardTermId(termId);
              void loadDashboard(termId);
            }}>
              <option value="">All terms</option>
              {terms.map((term) => (
                <option key={term.id} value={term.id}>
                  {term.name} · {years.find((year) => year.id === term.academic_year_id)?.name ?? ""}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!dashboard || dashboard.summaries.length === 0 ? (
          <p className="billing-empty">No issued invoices match this filter.</p>
        ) : (
          <div className="billing-summary-grid">
            {dashboard.summaries.map((summary) => (
              <article className="billing-summary-card" key={summary.currency}>
                <div className="billing-summary-head"><strong>{summary.currency}</strong><span>{summary.invoice_count} invoices</span></div>
                <div className="billing-summary-metrics">
                  <div><span>Collected</span><strong>{money(summary.total_collected, summary.currency)}</strong></div>
                  <div><span>Outstanding</span><strong>{money(summary.total_outstanding, summary.currency)}</strong></div>
                  <div><span>Collection rate</span><strong>{Number(summary.collection_rate).toFixed(1)}%</strong></div>
                </div>
                <div className="billing-progress" role="progressbar" aria-label={`${summary.currency} collection rate`} aria-valuenow={Math.min(Number(summary.collection_rate), 100)} aria-valuemin={0} aria-valuemax={100}>
                  <span style={{ width: `${Math.min(Math.max(Number(summary.collection_rate), 0), 100)}%` }} />
                </div>
                <small className="billing-summary-foot">
                  {summary.overdue_invoice_count} overdue · {money(summary.total_invoiced, summary.currency)} invoiced
                </small>
              </article>
            ))}
          </div>
        )}
        <div className="billing-defaulters-heading">
          <div><strong>Overdue accounts</strong><span>{dashboard?.defaulters_total ?? 0} unpaid overdue invoice(s)</span></div>
        </div>
        {!dashboard?.defaulters.length ? (
          <p className="billing-empty">No overdue balances for this selection.</p>
        ) : (
          <div className="table-scroll billing-table-scroll">
            <table><thead><tr><th>Student</th><th>Invoice</th><th>Class / term</th><th>Due</th><th>Overdue</th><th>Balance</th><th /></tr></thead>
              <tbody>{dashboard.defaulters.map((defaulter) => (
                <tr key={defaulter.invoice_id}>
                  <td>{defaulter.student_name}<small className="billing-subline">{defaulter.admission_number}</small></td>
                  <td>{defaulter.invoice_number}</td>
                  <td>{defaulter.class_name}<small className="billing-subline">{defaulter.term_name}</small></td>
                  <td>{defaulter.due_date}</td>
                  <td>{defaulter.days_overdue} day(s)</td>
                  <td>{money(defaulter.balance, defaulter.currency)}</td>
                  <td><button className="text-button" type="button" onClick={() => {
                    setPaymentInvoiceId(defaulter.invoice_id);
                    setPaymentAmount(String(defaulter.balance));
                    document.getElementById("billing-invoices")?.scrollIntoView({ behavior: "smooth" });
                  }}>Record payment</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel billing-card">
        <div className="panel-header">
          <div><h2>Payment settings</h2><p>Credentials are encrypted at rest and never shown again after saving.</p></div>
          <CreditCard size={19} className="billing-icon" />
        </div>
        <form className="billing-form-grid" onSubmit={(event) => void saveSettings(event)}>
          <label>Payment provider
            <select value={provider} onChange={(event) => setProvider(event.target.value as BillingSettings["provider"])}>
              <option value="manual">Manual payments</option>
              <option value="paystack">Paystack</option>
              <option value="stripe">Stripe</option>
            </select>
          </label>
          <label>Tenant currency
            <input value={currency} onChange={(event) => setCurrency(event.target.value.replace(/[^a-z]/gi, "").slice(0, 3).toUpperCase())} minLength={3} maxLength={3} required aria-label="Tenant currency (three-letter code)" />
          </label>
          {provider !== "manual" && (
            <>
              {provider === "paystack" && <label>Paystack public key
                <input value={publicKey} onChange={(event) => setPublicKey(event.target.value)} placeholder="pk_live_…" autoComplete="off" />
              </label>}
              <label>Provider secret key
                <input type="password" value={secretKey} onChange={(event) => setSecretKey(event.target.value)} placeholder={settings?.provider === provider && settings.has_secret_key ? "Saved — enter to replace" : "Enter secret key"} autoComplete="new-password" />
              </label>
              {provider === "stripe" && <label>Webhook signing secret
                <input type="password" value={webhookSecret} onChange={(event) => setWebhookSecret(event.target.value)} placeholder={settings?.provider === provider && settings.has_webhook_secret ? "Saved — enter to replace" : "Enter webhook secret"} autoComplete="new-password" />
              </label>}
            </>
          )}
          <div className="billing-form-actions">
            <span className={settings?.configured ? "billing-configured" : "billing-unconfigured"}>
              {settings?.configured ? <><CheckCircle2 size={14} /> Ready</> : "Credentials needed for online checkout"}
            </span>
            <button className="button button-primary" type="submit" disabled={saving || currency.length !== 3}>Save settings</button>
          </div>
        </form>
        {provider === "paystack" && <p className="billing-help">Set the Paystack webhook URL to this school's API domain at <code>/api/v1/billing/webhooks/paystack</code>. Paystack signatures are verified using the provider secret key.</p>}
        {provider === "stripe" && <p className="billing-help">Set the Stripe webhook URL to this school's API domain at <code>/api/v1/billing/webhooks/stripe</code>, subscribe to <code>checkout.session.completed</code>, and save its signing secret here.</p>}
      </section>

      <section className="panel billing-card" id="billing-invoices">
        <div className="panel-header">
          <div><h2>Fee structures</h2><p>Set the charges that will be copied to each student's term invoice.</p></div>
          <Wallet size={19} className="billing-icon" />
        </div>
        <form className="billing-form-grid billing-form-fees" onSubmit={(event) => void createFee(event)}>
          <label>Class
            <select value={feeClassId} onChange={(event) => { setFeeClassId(event.target.value); setFeeTermId(""); }} required>
              {classes.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.academic_year}</option>)}
            </select>
          </label>
          <label>Term
            <select value={feeTermId} onChange={(event) => setFeeTermId(event.target.value)} required>
              <option value="">Select term</option>
              {feeTerms.map((term) => <option key={term.id} value={term.id}>{term.name}</option>)}
            </select>
          </label>
          <label>Fee description<input value={feeDescription} onChange={(event) => setFeeDescription(event.target.value)} maxLength={120} required placeholder="Tuition" /></label>
          <label>Amount ({currency})<input type="number" min="0.01" step="0.01" value={feeAmount} onChange={(event) => setFeeAmount(event.target.value)} required /></label>
          <button className="button button-primary billing-add-button" type="submit" disabled={saving || !feeTermId}><Plus size={15} /> Add fee</button>
        </form>
        {fees.length === 0 ? <p className="billing-empty">No fee structures yet. Add a fee item above to get started.</p> : (
          <div className="table-scroll billing-table-scroll">
            <table><thead><tr><th>Fee</th><th>Class / year</th><th>Term</th><th>Amount</th><th>Status</th><th /></tr></thead>
              <tbody>{fees.map((fee) => (
                <tr key={fee.id}>
                  <td>{fee.description}</td><td>{fee.class_name} · {fee.academic_year}</td><td>{fee.term_name}</td>
                  <td>{money(fee.amount, fee.currency)}</td><td>{fee.is_active ? "Active" : "Inactive"}</td>
                  <td><button className="text-button" type="button" onClick={() => void toggleFee(fee)} disabled={saving}>{fee.is_active ? "Deactivate" : "Reactivate"}</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>

      <section className="panel billing-card">
        <div className="panel-header">
          <div><h2>Generate term invoices</h2><p>One invoice per active student enrollment; repeat runs skip existing invoices.</p></div>
        </div>
        <form className="billing-form-grid billing-form-invoices" onSubmit={(event) => void generateInvoices(event)}>
          <label>Class
            <select value={invoiceClassId} onChange={(event) => { setInvoiceClassId(event.target.value); setInvoiceTermId(""); }} required>
              {classes.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.academic_year}</option>)}
            </select>
          </label>
          <label>Term
            <select value={invoiceTermId} onChange={(event) => setInvoiceTermId(event.target.value)} required>
              <option value="">Select term</option>
              {invoiceTerms.map((term) => <option key={term.id} value={term.id}>{term.name}</option>)}
            </select>
          </label>
          <label>Due date<input type="date" value={invoiceDueDate} onChange={(event) => setInvoiceDueDate(event.target.value)} required /></label>
          <button className="button button-primary billing-add-button" type="submit" disabled={saving || !invoiceTermId}><Plus size={15} /> Generate invoices</button>
        </form>
      </section>

      <section className="panel billing-card">
        <div className="panel-header"><div><h2>Invoices &amp; payments</h2><p>{invoices.length} invoice(s) · recorded payments are retained for audit history.</p></div></div>
        {paymentInvoiceId && (
          <form className="billing-payment-form" onSubmit={(event) => void recordPayment(event)}>
            <strong>Record a manual payment</strong>
            <label>Amount<input type="number" min="0.01" step="0.01" value={paymentAmount} onChange={(event) => setPaymentAmount(event.target.value)} required /></label>
            <label>Method<select value={paymentMethod} onChange={(event) => setPaymentMethod(event.target.value as "cash" | "bank_transfer")}><option value="bank_transfer">Bank transfer</option><option value="cash">Cash</option></select></label>
            <label>Reference<input value={paymentReference} onChange={(event) => setPaymentReference(event.target.value)} maxLength={128} /></label>
            <button className="button button-primary" type="submit" disabled={saving}>Save payment</button>
            <button className="button button-secondary" type="button" onClick={() => setPaymentInvoiceId("")}>Cancel</button>
          </form>
        )}
        {invoices.length === 0 ? <p className="billing-empty">No invoices have been generated yet.</p> : (
          <div className="table-scroll billing-table-scroll">
            <table><thead><tr><th>Invoice / student</th><th>Class / term</th><th>Total</th><th>Balance</th><th>Status</th><th>Payments</th><th /></tr></thead>
              <tbody>{invoices.map((invoice) => (
                <tr key={invoice.id}>
                  <td><strong>{invoice.invoice_number}</strong><small className="billing-subline">{invoice.student_name} · {invoice.admission_number}</small></td>
                  <td>{invoice.class_name}<small className="billing-subline">{invoice.term_name}</small></td>
                  <td>{money(invoice.amount, invoice.currency)}</td><td>{money(invoice.balance, invoice.currency)}</td>
                  <td><span className={`billing-status billing-status-${invoice.status}`}>{invoice.status}</span></td>
                  <td>{invoice.payments.length === 0 ? "—" : invoice.payments.map((payment) => (
                    <span className="billing-payment-entry" key={payment.id}>
                      {money(payment.amount, invoice.currency)} · {payment.method}
                      {payment.voided_at ? " · voided" : (
                        <button className="text-button billing-void-button" type="button" onClick={() => setPaymentToVoid(payment.id)}>Void</button>
                      )}
                    </span>
                  ))}</td>
                  <td>{Number(invoice.balance) > 0 && <button className="text-button" type="button" onClick={() => { setPaymentInvoiceId(invoice.id); setPaymentAmount(String(invoice.balance)); }}>Record payment</button>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
      {years.length === 0 && <p className="billing-help">Create an academic year before setting up term fees.</p>}
      <FeedbackModal
        message={paymentToVoid ? "Void this payment? The action will be recorded and cannot be undone." : notice}
        tone={paymentToVoid ? "confirm" : notice.endsWith("saved.") || notice.endsWith("added.") || notice.startsWith("Payment ") ? "success" : "info"}
        onClose={() => { setPaymentToVoid(""); setNotice(""); }}
        onConfirm={paymentToVoid ? () => void voidPayment(paymentToVoid) : undefined}
        confirmLabel="Void payment"
        confirming={saving && Boolean(paymentToVoid)}
      />
    </div>
  );
}
