"use client";

import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  AlertTriangle,
  Bus,
  Clock3,
  MapPinned,
  Plus,
  RefreshCw,
  Search,
  UserRound,
} from "lucide-react";
import { schoolApi, type Student, type StudentList } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type TransportRoute = {
  id: string;
  name: string;
  vehicle_number: string;
  driver_name: string;
  pickup_point: string;
  dropoff_point: string;
  capacity: number;
  status: "active" | "inactive";
  notes: string | null;
  created_at: string;
  updated_at: string;
};

type TransportAssignment = {
  id: string;
  route_id: string;
  route_name: string;
  student_id: string;
  student_name: string;
  pickup_point: string;
  dropoff_point: string;
  pickup_time: string | null;
  dropoff_time: string | null;
  status: "assigned" | "active" | "inactive";
  notes: string | null;
  created_at: string;
  updated_at: string;
};

type TransportLog = {
  id: string;
  route_id: string;
  route_name: string;
  student_id: string;
  student_name: string;
  event_type: "pickup" | "dropoff" | "delay" | "note";
  event_time: string;
  status: "on_time" | "late" | "missed";
  note: string | null;
  created_by_user_id: string | null;
  created_at: string;
};

type TransportDashboard = {
  active_routes: number;
  assigned_students: number;
  upcoming_pickups: number;
  late_logs: number;
};

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export default function TransportWorkspace() {
  const [dashboard, setDashboard] = useState<TransportDashboard | null>(null);
  const [routes, setRoutes] = useState<TransportRoute[]>([]);
  const [assignments, setAssignments] = useState<TransportAssignment[]>([]);
  const [logs, setLogs] = useState<TransportLog[]>([]);
  const [students, setStudents] = useState<Student[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");
  const [routeModalOpen, setRouteModalOpen] = useState(false);
  const [assignmentModalOpen, setAssignmentModalOpen] = useState(false);
  const [logModalOpen, setLogModalOpen] = useState(false);
  const [routePending, setRoutePending] = useState(false);
  const [assignmentPending, setAssignmentPending] = useState(false);
  const [logPending, setLogPending] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: "error" | "success" | "info" } | null>(null);
  const [routeForm, setRouteForm] = useState({
    name: "",
    vehicle_number: "",
    driver_name: "",
    pickup_point: "",
    dropoff_point: "",
    capacity: "12",
    notes: "",
  });
  const [assignmentForm, setAssignmentForm] = useState({
    route_id: "",
    student_id: "",
    pickup_point: "",
    dropoff_point: "",
    pickup_time: "",
    dropoff_time: "",
    notes: "",
  });
  const [logForm, setLogForm] = useState({
    route_id: "",
    student_id: "",
    event_type: "pickup",
    status: "on_time",
    note: "",
  });

  const filteredRoutes = useMemo(() => {
    if (!search.trim()) return routes;
    const query = search.toLowerCase();
    return routes.filter((route) => `${route.name} ${route.driver_name} ${route.vehicle_number} ${route.pickup_point} ${route.dropoff_point}`.toLowerCase().includes(query));
  }, [routes, search]);

  async function loadWorkspace(isRefresh = false) {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    try {
      const [dashboardData, routeData, assignmentData, logData, studentPage] = await Promise.all([
        schoolApi<TransportDashboard>("/api/v1/transport/dashboard"),
        schoolApi<{ items: TransportRoute[]; total: number }>("/api/v1/transport/routes"),
        schoolApi<{ items: TransportAssignment[]; total: number }>("/api/v1/transport/assignments"),
        schoolApi<{ items: TransportLog[]; total: number }>("/api/v1/transport/logs"),
        schoolApi<StudentList>("/api/v1/students?include_inactive=false&offset=0&limit=200"),
      ]);
      setDashboard(dashboardData);
      setRoutes(routeData.items);
      setAssignments(assignmentData.items);
      setLogs(logData.items);
      setStudents(studentPage.items);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to load the transport workspace.",
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

  async function submitRoute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRoutePending(true);
    try {
      const created = await schoolApi<TransportRoute>("/api/v1/transport/routes", {
        method: "POST",
        body: JSON.stringify({
          name: routeForm.name,
          vehicle_number: routeForm.vehicle_number,
          driver_name: routeForm.driver_name,
          pickup_point: routeForm.pickup_point,
          dropoff_point: routeForm.dropoff_point,
          capacity: Number(routeForm.capacity),
          notes: routeForm.notes.trim() || null,
        }),
      });
      setRoutes((current) => [created, ...current]);
      setRouteModalOpen(false);
      setRouteForm({
        name: "",
        vehicle_number: "",
        driver_name: "",
        pickup_point: "",
        dropoff_point: "",
        capacity: "12",
        notes: "",
      });
      setNotice({ message: "Transport route created successfully.", tone: "success" });
      setDashboard((current) => (current ? { ...current, active_routes: current.active_routes + 1 } : current));
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to save the route.",
        tone: "error",
      });
    } finally {
      setRoutePending(false);
    }
  }

  async function submitAssignment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAssignmentPending(true);
    try {
      const created = await schoolApi<TransportAssignment>("/api/v1/transport/assignments", {
        method: "POST",
        body: JSON.stringify({
          route_id: assignmentForm.route_id,
          student_id: assignmentForm.student_id,
          pickup_point: assignmentForm.pickup_point,
          dropoff_point: assignmentForm.dropoff_point,
          pickup_time: assignmentForm.pickup_time || null,
          dropoff_time: assignmentForm.dropoff_time || null,
          notes: assignmentForm.notes.trim() || null,
        }),
      });
      setAssignments((current) => [created, ...current]);
      setAssignmentModalOpen(false);
      setAssignmentForm({
        route_id: "",
        student_id: "",
        pickup_point: "",
        dropoff_point: "",
        pickup_time: "",
        dropoff_time: "",
        notes: "",
      });
      setNotice({ message: "Student assigned to the route.", tone: "success" });
      const refreshed = await schoolApi<TransportDashboard>("/api/v1/transport/dashboard");
      setDashboard(refreshed);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to assign the student.",
        tone: "error",
      });
    } finally {
      setAssignmentPending(false);
    }
  }

  async function submitLog(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLogPending(true);
    try {
      const created = await schoolApi<TransportLog>("/api/v1/transport/logs", {
        method: "POST",
        body: JSON.stringify({
          route_id: logForm.route_id,
          student_id: logForm.student_id,
          event_type: logForm.event_type,
          status: logForm.status,
          note: logForm.note.trim() || null,
        }),
      });
      setLogs((current) => [created, ...current]);
      setLogModalOpen(false);
      setLogForm({
        route_id: "",
        student_id: "",
        event_type: "pickup",
        status: "on_time",
        note: "",
      });
      setNotice({ message: "Transport log recorded.", tone: "success" });
      const refreshed = await schoolApi<TransportDashboard>("/api/v1/transport/dashboard");
      setDashboard(refreshed);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to save the log.",
        tone: "error",
      });
    } finally {
      setLogPending(false);
    }
  }

  return (
    <div className="transport-workspace">
      <section className="panel transport-hero">
        <div>
          <p className="eyebrow">TRANSPORT OPERATIONS</p>
          <h2>Routes, assignments & movement logs</h2>
        </div>
        <div className="transport-hero-actions">
          <button type="button" className="button button-secondary" onClick={() => void loadWorkspace(true)} disabled={refreshing}>
            <RefreshCw size={14} className={refreshing ? "analytics-refreshing" : ""} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          <button type="button" className="button button-primary" onClick={() => setRouteModalOpen(true)}>
            <Plus size={15} /> Add route
          </button>
        </div>
      </section>

      {dashboard && (
        <section className="transport-stat-grid" aria-label="Transport summary">
          <article className="panel transport-stat-card">
            <span className="analytics-stat-icon analytics-purple"><Bus size={17} /></span>
            <span className="analytics-stat-label">Active routes</span>
            <strong>{dashboard.active_routes}</strong>
            <small>in service</small>
          </article>
          <article className="panel transport-stat-card">
            <span className="analytics-stat-icon analytics-green"><UserRound size={17} /></span>
            <span className="analytics-stat-label">Assigned students</span>
            <strong>{dashboard.assigned_students}</strong>
            <small>on the list</small>
          </article>
          <article className="panel transport-stat-card">
            <span className="analytics-stat-icon analytics-orange"><Clock3 size={17} /></span>
            <span className="analytics-stat-label">Upcoming pickups</span>
            <strong>{dashboard.upcoming_pickups}</strong>
            <small>scheduled</small>
          </article>
          <article className="panel transport-stat-card">
            <span className="analytics-stat-icon analytics-red"><AlertTriangle size={17} /></span>
            <span className="analytics-stat-label">Late logs</span>
            <strong>{dashboard.late_logs}</strong>
            <small>needs attention</small>
          </article>
        </section>
      )}

      <section className="transport-grid">
        <article className="panel transport-card">
          <div className="transport-card-header">
            <div>
              <p className="eyebrow">ROUTE MANAGEMENT</p>
              <h3>School transport routes</h3>
            </div>
            <button type="button" className="button button-secondary" onClick={() => setAssignmentModalOpen(true)}>
              <Plus size={14} /> Assign student
            </button>
          </div>
          <div className="transport-search">
            <Search size={14} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search a route, driver, or stop" />
          </div>
          {loading ? (
            <p className="transport-empty">Loading routes…</p>
          ) : filteredRoutes.length === 0 ? (
            <p className="transport-empty">No routes found.</p>
          ) : (
            <div className="transport-table-wrap">
              <table className="transport-table">
                <thead>
                  <tr>
                    <th>Route</th>
                    <th>Driver</th>
                    <th>Stops</th>
                    <th>Capacity</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredRoutes.map((route) => (
                    <tr key={route.id}>
                      <td>
                        <strong>{route.name}</strong>
                        <small>{route.vehicle_number}</small>
                      </td>
                      <td>{route.driver_name}</td>
                      <td>
                        <span>{route.pickup_point}</span>
                        <small>→ {route.dropoff_point}</small>
                      </td>
                      <td>{route.capacity}</td>
                      <td>
                        <span className={`transport-status ${route.status === "active" ? "active" : "inactive"}`}>
                          {route.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>

        <article className="panel transport-card">
          <div className="transport-card-header">
            <div>
              <p className="eyebrow">ASSIGNMENTS</p>
              <h3>Route coverage</h3>
            </div>
            <button type="button" className="button button-secondary" onClick={() => setLogModalOpen(true)}>
              <Plus size={14} /> Add log
            </button>
          </div>
          {loading ? (
            <p className="transport-empty">Loading assignments…</p>
          ) : assignments.length === 0 ? (
            <p className="transport-empty">No student assignments yet.</p>
          ) : (
            <div className="transport-list">
              {assignments.map((assignment) => (
                <div className="transport-item" key={assignment.id}>
                  <div className="transport-item-main">
                    <strong>{assignment.student_name}</strong>
                    <span>{assignment.route_name}</span>
                  </div>
                  <div className="transport-item-meta">
                    <span><MapPinned size={13} /> {assignment.pickup_point} → {assignment.dropoff_point}</span>
                    {assignment.pickup_time ? <span><Clock3 size={13} /> {assignment.pickup_time}</span> : null}
                  </div>
                  <span className={`transport-status ${assignment.status === "assigned" ? "assigned" : assignment.status === "active" ? "active" : "inactive"}`}>
                    {assignment.status}
                  </span>
                </div>
              ))}
            </div>
          )}
        </article>
      </section>

      <article className="panel transport-card">
        <div className="transport-card-header">
          <div>
            <p className="eyebrow">MOVEMENT LOGS</p>
            <h3>Recent pick-ups and issues</h3>
          </div>
        </div>
        {loading ? (
          <p className="transport-empty">Loading logs…</p>
        ) : logs.length === 0 ? (
          <p className="transport-empty">No transport logs yet.</p>
        ) : (
          <div className="transport-log-list">
            {logs.map((log) => (
              <div className="transport-log-item" key={log.id}>
                <div>
                  <strong>{log.student_name}</strong>
                  <small>{log.route_name}</small>
                </div>
                <div>
                  <span className="transport-log-type">{log.event_type}</span>
                  <small>{formatDateTime(log.event_time)}</small>
                </div>
                <span className={`transport-status ${log.status === "late" ? "late" : log.status === "missed" ? "missed" : "active"}`}>
                  {log.status}
                </span>
              </div>
            ))}
          </div>
        )}
      </article>

      {routeModalOpen && (
        <div className="library-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setRouteModalOpen(false)}>
          <div className="library-modal" role="dialog" aria-modal="true">
            <div className="library-modal-header">
              <h3>Add transport route</h3>
              <button type="button" className="icon-button" aria-label="Close route dialog" onClick={() => setRouteModalOpen(false)}>×</button>
            </div>
            <form className="library-form-grid" onSubmit={(event) => void submitRoute(event)}>
              <label>
                Route name
                <input value={routeForm.name} onChange={(event) => setRouteForm((current) => ({ ...current, name: event.target.value }))} required />
              </label>
              <label>
                Vehicle number
                <input value={routeForm.vehicle_number} onChange={(event) => setRouteForm((current) => ({ ...current, vehicle_number: event.target.value }))} required />
              </label>
              <label>
                Driver name
                <input value={routeForm.driver_name} onChange={(event) => setRouteForm((current) => ({ ...current, driver_name: event.target.value }))} required />
              </label>
              <label>
                Capacity
                <input type="number" min="1" value={routeForm.capacity} onChange={(event) => setRouteForm((current) => ({ ...current, capacity: event.target.value }))} required />
              </label>
              <label>
                Pickup point
                <input value={routeForm.pickup_point} onChange={(event) => setRouteForm((current) => ({ ...current, pickup_point: event.target.value }))} required />
              </label>
              <label>
                Drop-off point
                <input value={routeForm.dropoff_point} onChange={(event) => setRouteForm((current) => ({ ...current, dropoff_point: event.target.value }))} required />
              </label>
              <label className="library-notes-field">
                Notes
                <textarea rows={3} value={routeForm.notes} onChange={(event) => setRouteForm((current) => ({ ...current, notes: event.target.value }))} />
              </label>
              <div className="library-modal-actions">
                <button type="button" className="button button-secondary" onClick={() => setRouteModalOpen(false)}>Cancel</button>
                <button type="submit" className="button button-primary" disabled={routePending}>{routePending ? "Saving…" : "Save route"}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {assignmentModalOpen && (
        <div className="library-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setAssignmentModalOpen(false)}>
          <div className="library-modal" role="dialog" aria-modal="true">
            <div className="library-modal-header">
              <h3>Assign student</h3>
              <button type="button" className="icon-button" aria-label="Close assignment dialog" onClick={() => setAssignmentModalOpen(false)}>×</button>
            </div>
            <form className="library-form-grid" onSubmit={(event) => void submitAssignment(event)}>
              <label>
                Route
                <select value={assignmentForm.route_id} onChange={(event) => setAssignmentForm((current) => ({ ...current, route_id: event.target.value }))} required>
                  <option value="">Select a route</option>
                  {routes.map((route) => (
                    <option key={route.id} value={route.id}>{route.name}</option>
                  ))}
                </select>
              </label>
              <label>
                Student
                <select value={assignmentForm.student_id} onChange={(event) => setAssignmentForm((current) => ({ ...current, student_id: event.target.value }))} required>
                  <option value="">Select a student</option>
                  {students.map((student) => (
                    <option key={student.id} value={student.id}>{student.first_name} {student.last_name}</option>
                  ))}
                </select>
              </label>
              <label>
                Pickup point
                <input value={assignmentForm.pickup_point} onChange={(event) => setAssignmentForm((current) => ({ ...current, pickup_point: event.target.value }))} required />
              </label>
              <label>
                Drop-off point
                <input value={assignmentForm.dropoff_point} onChange={(event) => setAssignmentForm((current) => ({ ...current, dropoff_point: event.target.value }))} required />
              </label>
              <label>
                Pickup time
                <input type="time" value={assignmentForm.pickup_time} onChange={(event) => setAssignmentForm((current) => ({ ...current, pickup_time: event.target.value }))} />
              </label>
              <label>
                Drop-off time
                <input type="time" value={assignmentForm.dropoff_time} onChange={(event) => setAssignmentForm((current) => ({ ...current, dropoff_time: event.target.value }))} />
              </label>
              <label className="library-notes-field">
                Notes
                <textarea rows={3} value={assignmentForm.notes} onChange={(event) => setAssignmentForm((current) => ({ ...current, notes: event.target.value }))} />
              </label>
              <div className="library-modal-actions">
                <button type="button" className="button button-secondary" onClick={() => setAssignmentModalOpen(false)}>Cancel</button>
                <button type="submit" className="button button-primary" disabled={assignmentPending}>{assignmentPending ? "Assigning…" : "Assign student"}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {logModalOpen && (
        <div className="library-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setLogModalOpen(false)}>
          <div className="library-modal" role="dialog" aria-modal="true">
            <div className="library-modal-header">
              <h3>Add movement log</h3>
              <button type="button" className="icon-button" aria-label="Close log dialog" onClick={() => setLogModalOpen(false)}>×</button>
            </div>
            <form className="library-form-grid" onSubmit={(event) => void submitLog(event)}>
              <label>
                Route
                <select value={logForm.route_id} onChange={(event) => setLogForm((current) => ({ ...current, route_id: event.target.value }))} required>
                  <option value="">Select a route</option>
                  {routes.map((route) => (
                    <option key={route.id} value={route.id}>{route.name}</option>
                  ))}
                </select>
              </label>
              <label>
                Student
                <select value={logForm.student_id} onChange={(event) => setLogForm((current) => ({ ...current, student_id: event.target.value }))} required>
                  <option value="">Select a student</option>
                  {students.map((student) => (
                    <option key={student.id} value={student.id}>{student.first_name} {student.last_name}</option>
                  ))}
                </select>
              </label>
              <label>
                Event type
                <select value={logForm.event_type} onChange={(event) => setLogForm((current) => ({ ...current, event_type: event.target.value as "pickup" | "dropoff" | "delay" | "note" }))}>
                  <option value="pickup">Pickup</option>
                  <option value="dropoff">Dropoff</option>
                  <option value="delay">Delay</option>
                  <option value="note">Note</option>
                </select>
              </label>
              <label>
                Status
                <select value={logForm.status} onChange={(event) => setLogForm((current) => ({ ...current, status: event.target.value as "on_time" | "late" | "missed" }))}>
                  <option value="on_time">On time</option>
                  <option value="late">Late</option>
                  <option value="missed">Missed</option>
                </select>
              </label>
              <label className="library-notes-field">
                Note
                <textarea rows={3} value={logForm.note} onChange={(event) => setLogForm((current) => ({ ...current, note: event.target.value }))} />
              </label>
              <div className="library-modal-actions">
                <button type="button" className="button button-secondary" onClick={() => setLogModalOpen(false)}>Cancel</button>
                <button type="submit" className="button button-primary" disabled={logPending}>{logPending ? "Saving…" : "Save log"}</button>
              </div>
            </form>
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
