"use client";

import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  BookOpenText,
  CircleDollarSign,
  Plus,
  RefreshCw,
  Search,
  ShieldAlert,
  UserRound,
} from "lucide-react";
import { schoolApi, type Student, type StudentList } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type LibrarySettings = {
  id: string;
  daily_fine_rate: number | string;
  currency: string;
  updated_at: string;
};

type LibraryBook = {
  id: string;
  title: string;
  author: string;
  isbn: string | null;
  category: string | null;
  shelf_location: string | null;
  total_copies: number;
  available_copies: number;
  status: string;
  created_at: string;
  updated_at: string;
};

type LibraryLoan = {
  id: string;
  book_id: string;
  book_title: string;
  student_id: string;
  student_name: string;
  borrower_name: string;
  issued_by_user_id: string;
  checked_out_at: string;
  due_date: string;
  returned_at: string | null;
  status: "active" | "returned";
  fine_amount: number | string;
  fine_currency: string;
  notes: string | null;
};

type LibraryDashboard = {
  total_books: number;
  available_books: number;
  active_loans: number;
  overdue_loans: number;
  total_fines_due: number | string;
  currency: string;
  daily_fine_rate: number | string;
};

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function formatMoney(value: number | string, currency: string): string {
  const numeric = Number(value ?? 0);
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency || "NGN",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(numeric) ? numeric : 0);
}

export default function LibraryWorkspace() {
  const [dashboard, setDashboard] = useState<LibraryDashboard | null>(null);
  const [settings, setSettings] = useState<LibrarySettings | null>(null);
  const [books, setBooks] = useState<LibraryBook[]>([]);
  const [loans, setLoans] = useState<LibraryLoan[]>([]);
  const [students, setStudents] = useState<Student[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [bookQuery, setBookQuery] = useState("");
  const [savePending, setSavePending] = useState(false);
  const [actionPending, setActionPending] = useState(false);
  const [bookModalOpen, setBookModalOpen] = useState(false);
  const [loanModalOpen, setLoanModalOpen] = useState(false);
  const [confirmReturn, setConfirmReturn] = useState<LibraryLoan | null>(null);
  const [notice, setNotice] = useState<{ message: string; tone: "error" | "success" | "info" } | null>(null);
  const [settingsForm, setSettingsForm] = useState({ daily_fine_rate: "0", currency: "NGN" });
  const [bookForm, setBookForm] = useState({
    title: "",
    author: "",
    isbn: "",
    category: "",
    shelf_location: "",
    total_copies: "1",
  });
  const [loanForm, setLoanForm] = useState({
    book_id: "",
    student_id: "",
    borrower_name: "",
    due_date: "",
    notes: "",
  });

  const filteredBooks = useMemo(
    () => books.filter((book) => {
      const haystack = `${book.title} ${book.author} ${book.isbn ?? ""} ${book.category ?? ""}`.toLowerCase();
      return haystack.includes(bookQuery.trim().toLowerCase());
    }),
    [bookQuery, books],
  );

  async function loadWorkspace(isRefresh = false) {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    try {
      const [dashboardData, settingsData, bookData, loanData, studentPage] = await Promise.all([
        schoolApi<LibraryDashboard>("/api/v1/library/dashboard"),
        schoolApi<LibrarySettings>("/api/v1/library/settings"),
        schoolApi<{ items: LibraryBook[]; total: number }>("/api/v1/library/books"),
        schoolApi<{ items: LibraryLoan[]; total: number }>("/api/v1/library/loans"),
        schoolApi<StudentList>("/api/v1/students?include_inactive=false&offset=0&limit=200"),
      ]);
      setDashboard(dashboardData);
      setSettings(settingsData);
      setSettingsForm({
        daily_fine_rate: String(settingsData.daily_fine_rate ?? 0),
        currency: settingsData.currency || "NGN",
      });
      setBooks(bookData.items);
      setLoans(loanData.items);
      setStudents(studentPage.items);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to load the library workspace.",
        tone: "error",
      });
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    void loadWorkspace();
  }, []);

  async function saveSettings(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavePending(true);
    try {
      const updated = await schoolApi<LibrarySettings>("/api/v1/library/settings", {
        method: "PUT",
        body: JSON.stringify({
          daily_fine_rate: Number(settingsForm.daily_fine_rate),
          currency: settingsForm.currency.toUpperCase(),
        }),
      });
      setSettings(updated);
      setSettingsForm({
        daily_fine_rate: String(updated.daily_fine_rate ?? 0),
        currency: updated.currency || "NGN",
      });
      setNotice({ message: "Library policy saved successfully.", tone: "success" });
      setDashboard((current) => current ? { ...current, daily_fine_rate: updated.daily_fine_rate, currency: updated.currency } : current);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to update library settings.",
        tone: "error",
      });
    } finally {
      setSavePending(false);
    }
  }

  async function submitBook(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionPending(true);
    try {
      const created = await schoolApi<LibraryBook>("/api/v1/library/books", {
        method: "POST",
        body: JSON.stringify({
          title: bookForm.title,
          author: bookForm.author,
          isbn: bookForm.isbn.trim() || null,
          category: bookForm.category.trim() || null,
          shelf_location: bookForm.shelf_location.trim() || null,
          total_copies: Number(bookForm.total_copies),
        }),
      });
      setBooks((current) => [created, ...current]);
      setBookModalOpen(false);
      setBookForm({
        title: "",
        author: "",
        isbn: "",
        category: "",
        shelf_location: "",
        total_copies: "1",
      });
      setNotice({ message: "Library book added.", tone: "success" });
      setDashboard((current) => current ? { ...current, total_books: current.total_books + 1 } : current);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to add the book.",
        tone: "error",
      });
    } finally {
      setActionPending(false);
    }
  }

  async function submitLoan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setActionPending(true);
    try {
      const created = await schoolApi<LibraryLoan>("/api/v1/library/loans", {
        method: "POST",
        body: JSON.stringify({
          book_id: loanForm.book_id,
          student_id: loanForm.student_id,
          borrower_name: loanForm.borrower_name,
          due_date: loanForm.due_date,
          notes: loanForm.notes.trim() || null,
        }),
      });
      setLoans((current) => [created, ...current]);
      setLoanModalOpen(false);
      setLoanForm({ book_id: "", student_id: "", borrower_name: "", due_date: "", notes: "" });
      setNotice({ message: "Book issued to the student.", tone: "success" });
      const nextDashboard = await schoolApi<LibraryDashboard>("/api/v1/library/dashboard");
      setDashboard(nextDashboard);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to issue this book.",
        tone: "error",
      });
    } finally {
      setActionPending(false);
    }
  }

  async function handleReturnLoan(loan: LibraryLoan) {
    setActionPending(true);
    try {
      const updated = await schoolApi<LibraryLoan>(`/api/v1/library/loans/${encodeURIComponent(loan.id)}/return`, {
        method: "POST",
      });
      setLoans((current) => current.map((item) => item.id === loan.id ? updated : item));
      setConfirmReturn(null);
      setNotice({ message: `Book returned and fine recorded: ${formatMoney(updated.fine_amount, updated.fine_currency)}.`, tone: "success" });
      const nextDashboard = await schoolApi<LibraryDashboard>("/api/v1/library/dashboard");
      setDashboard(nextDashboard);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to record the return.",
        tone: "error",
      });
    } finally {
      setActionPending(false);
    }
  }

  const currentFineRate = Number(dashboard?.daily_fine_rate ?? settings?.daily_fine_rate ?? 0);

  return (
    <div className="library-workspace">
      <section className="panel library-hero">
        <div>
          <p className="eyebrow">LIBRARY OPERATIONS</p>
          <h2>Reading records & circulation</h2>
        </div>
        <div className="library-hero-actions">
          <button type="button" className="button button-secondary" onClick={() => void loadWorkspace(true)} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? "analytics-refreshing" : ""} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          <button type="button" className="button button-primary" onClick={() => setBookModalOpen(true)}>
            <Plus size={15} /> Add book
          </button>
        </div>
      </section>

      {dashboard && (
        <section className="library-stat-grid" aria-label="Library summary">
          <article className="panel library-stat-card">
            <span className="analytics-stat-icon analytics-purple"><BookOpenText size={17} /></span>
            <span className="analytics-stat-label">Books</span>
            <strong>{dashboard.total_books}</strong>
            <small>{dashboard.available_books} available copies</small>
          </article>
          <article className="panel library-stat-card">
            <span className="analytics-stat-icon analytics-green"><UserRound size={17} /></span>
            <span className="analytics-stat-label">Active loans</span>
            <strong>{dashboard.active_loans}</strong>
            <small>{dashboard.overdue_loans} overdue</small>
          </article>
          <article className="panel library-stat-card">
            <span className="analytics-stat-icon analytics-orange"><ShieldAlert size={17} /></span>
            <span className="analytics-stat-label">Fine policy</span>
            <strong>{formatMoney(currentFineRate, dashboard.currency || settings?.currency || "NGN")}</strong>
            <small>per day</small>
          </article>
          <article className="panel library-stat-card">
            <span className="analytics-stat-icon analytics-blue"><CircleDollarSign size={17} /></span>
            <span className="analytics-stat-label">Outstanding fines</span>
            <strong>{formatMoney(dashboard.total_fines_due, dashboard.currency || settings?.currency || "NGN")}</strong>
            <small>across active loans</small>
          </article>
        </section>
      )}

      <section className="library-grid">
        <article className="panel library-card">
          <div className="library-card-header">
            <div>
              <p className="eyebrow">FINE POLICY</p>
              <h3>Library settings</h3>
            </div>
          </div>
          <form className="library-form" onSubmit={(event) => void saveSettings(event)}>
            <label>
              Daily fine rate
              <input
                type="number"
                min="0"
                step="0.01"
                value={settingsForm.daily_fine_rate}
                onChange={(event) => setSettingsForm((current) => ({ ...current, daily_fine_rate: event.target.value }))}
              />
            </label>
            <label>
              Currency
              <input
                maxLength={3}
                value={settingsForm.currency}
                onChange={(event) => setSettingsForm((current) => ({ ...current, currency: event.target.value.toUpperCase() }))}
              />
            </label>
            <button className="button button-primary" type="submit" disabled={savePending}>
              {savePending ? "Saving…" : "Save policy"}
            </button>
          </form>
        </article>

        <article className="panel library-card">
          <div className="library-card-header">
            <div>
              <p className="eyebrow">CHECKOUT</p>
              <h3>Issue a book</h3>
            </div>
            <button type="button" className="button button-secondary" onClick={() => setLoanModalOpen(true)}>
              <Plus size={14} /> New loan
            </button>
          </div>
          {loading ? <p className="library-empty">Loading active loans…</p> : loans.length === 0 ? (
            <p className="library-empty">No active borrow records yet.</p>
          ) : (
            <div className="library-table-wrap">
              <table className="library-table">
                <thead>
                  <tr>
                    <th>Student</th>
                    <th>Book</th>
                    <th>Due</th>
                    <th>Fine</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {loans.map((loan) => (
                    <tr key={loan.id}>
                      <td>
                        <strong>{loan.student_name}</strong>
                        <small>{loan.borrower_name}</small>
                      </td>
                      <td>{loan.book_title}</td>
                      <td>{formatDate(loan.due_date)}</td>
                      <td>{loan.status === "returned" ? formatMoney(loan.fine_amount, loan.fine_currency) : formatMoney(Number(loan.fine_amount) || 0, loan.fine_currency)}</td>
                      <td>
                        {loan.status === "active" ? (
                          <button type="button" className="button button-secondary small-button" onClick={() => setConfirmReturn(loan)}>
                            Return
                          </button>
                        ) : (
                          <span className="library-status returned">Returned</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>
      </section>

      <article className="panel library-card">
        <div className="library-card-header">
          <div>
            <p className="eyebrow">CATALOGUE</p>
            <h3>Available books</h3>
          </div>
        </div>
        <div className="library-search">
          <Search size={14} />
          <input value={bookQuery} onChange={(event) => setBookQuery(event.target.value)} placeholder="Search by title, author, or ISBN" />
        </div>
        {filteredBooks.length === 0 ? (
          <p className="library-empty">No books match your search yet.</p>
        ) : (
          <div className="library-book-grid">
            {filteredBooks.map((book) => (
              <article className="library-book-item" key={book.id}>
                <div className="library-book-topline">
                  <span className="library-book-tag">{book.category || "General"}</span>
                  <span className={`library-status ${book.status === "available" ? "available" : "archived"}`}>
                    {book.status}
                  </span>
                </div>
                <h4>{book.title}</h4>
                <p>{book.author}</p>
                <ul>
                  <li>Copies: {book.total_copies}</li>
                  <li>Available: {book.available_copies}</li>
                  <li>Shelf: {book.shelf_location || "Unassigned"}</li>
                  {book.isbn ? <li>ISBN: {book.isbn}</li> : null}
                </ul>
              </article>
            ))}
          </div>
        )}
      </article>

      {bookModalOpen && (
        <div className="library-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setBookModalOpen(false)}>
          <div className="library-modal" role="dialog" aria-modal="true">
            <div className="library-modal-header">
              <h3>Add book</h3>
              <button type="button" className="icon-button" aria-label="Close book dialog" onClick={() => setBookModalOpen(false)}>×</button>
            </div>
            <form className="library-form-grid" onSubmit={(event) => void submitBook(event)}>
              <label>
                Title
                <input value={bookForm.title} onChange={(event) => setBookForm((current) => ({ ...current, title: event.target.value }))} required />
              </label>
              <label>
                Author
                <input value={bookForm.author} onChange={(event) => setBookForm((current) => ({ ...current, author: event.target.value }))} required />
              </label>
              <label>
                ISBN
                <input value={bookForm.isbn} onChange={(event) => setBookForm((current) => ({ ...current, isbn: event.target.value }))} />
              </label>
              <label>
                Category
                <input value={bookForm.category} onChange={(event) => setBookForm((current) => ({ ...current, category: event.target.value }))} />
              </label>
              <label>
                Shelf location
                <input value={bookForm.shelf_location} onChange={(event) => setBookForm((current) => ({ ...current, shelf_location: event.target.value }))} />
              </label>
              <label>
                Total copies
                <input type="number" min="1" value={bookForm.total_copies} onChange={(event) => setBookForm((current) => ({ ...current, total_copies: event.target.value }))} required />
              </label>
              <div className="library-modal-actions">
                <button type="button" className="button button-secondary" onClick={() => setBookModalOpen(false)}>Cancel</button>
                <button type="submit" className="button button-primary" disabled={actionPending}>
                  {actionPending ? "Saving…" : "Save book"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {loanModalOpen && (
        <div className="library-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setLoanModalOpen(false)}>
          <div className="library-modal" role="dialog" aria-modal="true">
            <div className="library-modal-header">
              <h3>Issue book</h3>
              <button type="button" className="icon-button" aria-label="Close issue loan dialog" onClick={() => setLoanModalOpen(false)}>×</button>
            </div>
            <form className="library-form-grid" onSubmit={(event) => void submitLoan(event)}>
              <label>
                Student
                <select
                  value={loanForm.student_id}
                  onChange={(event) => setLoanForm((current) => ({ ...current, student_id: event.target.value }))}
                  required
                >
                  <option value="">Select a student</option>
                  {students.map((student) => (
                    <option key={student.id} value={student.id}>
                      {student.first_name} {student.last_name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Book
                <select
                  value={loanForm.book_id}
                  onChange={(event) => setLoanForm((current) => ({ ...current, book_id: event.target.value }))}
                  required
                >
                  <option value="">Select a book</option>
                  {books.map((book) => (
                    <option key={book.id} value={book.id}>
                      {book.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Borrower name
                <input
                  value={loanForm.borrower_name}
                  onChange={(event) => setLoanForm((current) => ({ ...current, borrower_name: event.target.value }))}
                  required
                />
              </label>
              <label>
                Due date
                <input
                  type="date"
                  value={loanForm.due_date}
                  onChange={(event) => setLoanForm((current) => ({ ...current, due_date: event.target.value }))}
                  required
                />
              </label>
              <label className="library-notes-field">
                Notes
                <textarea rows={3} value={loanForm.notes} onChange={(event) => setLoanForm((current) => ({ ...current, notes: event.target.value }))} />
              </label>
              <div className="library-modal-actions">
                <button type="button" className="button button-secondary" onClick={() => setLoanModalOpen(false)}>Cancel</button>
                <button type="submit" className="button button-primary" disabled={actionPending}>
                  {actionPending ? "Saving…" : "Issue book"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {confirmReturn && (
        <div className="library-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setConfirmReturn(null)}>
          <div className="library-modal confirmation-modal" role="dialog" aria-modal="true">
            <div className="library-modal-header">
              <h3>Confirm return</h3>
              <button type="button" className="icon-button" aria-label="Close return confirmation" onClick={() => setConfirmReturn(null)}>×</button>
            </div>
            <p>
              Mark <strong>{confirmReturn.book_title}</strong> returned from <strong>{confirmReturn.student_name}</strong> and calculate the overdue fine?
            </p>
            <div className="library-modal-actions">
              <button type="button" className="button button-secondary" onClick={() => setConfirmReturn(null)}>Cancel</button>
              <button type="button" className="button button-primary" onClick={() => void handleReturnLoan(confirmReturn)} disabled={actionPending}>
                {actionPending ? "Updating…" : "Mark returned"}
              </button>
            </div>
          </div>
        </div>
      )}

      {notice && (
        <FeedbackModal
          message={notice.message}
          tone={notice.tone}
          onClose={() => setNotice(null)}
        />
      )}
    </div>
  );
}
