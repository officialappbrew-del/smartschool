"use client";

import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  BedDouble,
  Coins,
  Home,
  Plus,
  RefreshCw,
  Search,
  Users,
} from "lucide-react";
import { schoolApi, type Student, type StudentList } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type BoardingRoom = {
  id: string;
  name: string;
  room_type: "single" | "double" | "dormitory";
  capacity: number;
  occupied_count: number;
  available_slots: number;
  monthly_fee: number | string;
  status: "available" | "full" | "maintenance";
  notes: string | null;
  created_at: string;
  updated_at: string;
};

type BoardingAssignment = {
  id: string;
  room_id: string;
  room_name: string;
  student_id: string;
  student_name: string;
  bed_number: string | null;
  check_in_date: string;
  room_fee: number | string;
  status: "active" | "pending" | "left";
  notes: string | null;
  created_at: string;
  updated_at: string;
};

type BoardingDashboard = {
  total_rooms: number;
  total_capacity: number;
  occupied_rooms: number;
  available_slots: number;
  active_residents: number;
  monthly_revenue: number | string;
};

function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function formatMoney(value: number | string, currency = "NGN"): string {
  const numeric = Number(value ?? 0);
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: currency || "NGN",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(numeric) ? numeric : 0);
}

export default function BoardingWorkspace() {
  const [dashboard, setDashboard] = useState<BoardingDashboard | null>(null);
  const [rooms, setRooms] = useState<BoardingRoom[]>([]);
  const [assignments, setAssignments] = useState<BoardingAssignment[]>([]);
  const [students, setStudents] = useState<Student[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [search, setSearch] = useState("");
  const [roomModalOpen, setRoomModalOpen] = useState(false);
  const [assignmentModalOpen, setAssignmentModalOpen] = useState(false);
  const [roomPending, setRoomPending] = useState(false);
  const [assignmentPending, setAssignmentPending] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: "error" | "success" | "info" } | null>(null);
  const [roomForm, setRoomForm] = useState({
    name: "",
    room_type: "single",
    capacity: "4",
    monthly_fee: "150000",
    notes: "",
  });
  const [assignmentForm, setAssignmentForm] = useState({
    room_id: "",
    student_id: "",
    bed_number: "",
    check_in_date: new Date().toISOString().slice(0, 10),
    room_fee: "150000",
    notes: "",
  });

  async function loadWorkspace(isRefresh = false) {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);
    try {
      const [dashboardData, roomData, assignmentData, studentPage] = await Promise.all([
        schoolApi<BoardingDashboard>("/api/v1/boarding/dashboard"),
        schoolApi<{ items: BoardingRoom[]; total: number }>("/api/v1/boarding/rooms"),
        schoolApi<{ items: BoardingAssignment[]; total: number }>("/api/v1/boarding/assignments"),
        schoolApi<StudentList>("/api/v1/students?include_inactive=false&offset=0&limit=200"),
      ]);
      setDashboard(dashboardData);
      setRooms(roomData.items);
      setAssignments(assignmentData.items);
      setStudents(studentPage.items);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to load the boarding workspace.",
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

  const filteredRooms = useMemo(() => {
    if (!search.trim()) return rooms;
    const query = search.toLowerCase();
    return rooms.filter((room) => `${room.name} ${room.room_type} ${room.status}`.toLowerCase().includes(query));
  }, [rooms, search]);

  async function submitRoom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRoomPending(true);
    try {
      const created = await schoolApi<BoardingRoom>("/api/v1/boarding/rooms", {
        method: "POST",
        body: JSON.stringify({
          name: roomForm.name,
          room_type: roomForm.room_type,
          capacity: Number(roomForm.capacity),
          monthly_fee: Number(roomForm.monthly_fee),
          notes: roomForm.notes.trim() || null,
        }),
      });
      setRooms((current) => [created, ...current]);
      setRoomModalOpen(false);
      setRoomForm({ name: "", room_type: "single", capacity: "4", monthly_fee: "150000", notes: "" });
      setNotice({ message: "Boarding room created successfully.", tone: "success" });
      const refreshed = await schoolApi<BoardingDashboard>("/api/v1/boarding/dashboard");
      setDashboard(refreshed);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to save the room.",
        tone: "error",
      });
    } finally {
      setRoomPending(false);
    }
  }

  async function submitAssignment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAssignmentPending(true);
    try {
      const created = await schoolApi<BoardingAssignment>("/api/v1/boarding/assignments", {
        method: "POST",
        body: JSON.stringify({
          room_id: assignmentForm.room_id,
          student_id: assignmentForm.student_id,
          bed_number: assignmentForm.bed_number.trim() || null,
          check_in_date: assignmentForm.check_in_date,
          room_fee: Number(assignmentForm.room_fee),
          notes: assignmentForm.notes.trim() || null,
        }),
      });
      setAssignments((current) => [created, ...current]);
      setAssignmentModalOpen(false);
      setAssignmentForm({
        room_id: "",
        student_id: "",
        bed_number: "",
        check_in_date: new Date().toISOString().slice(0, 10),
        room_fee: "150000",
        notes: "",
      });
      setNotice({ message: "Student assigned to the boarding room.", tone: "success" });
      const refreshed = await schoolApi<BoardingDashboard>("/api/v1/boarding/dashboard");
      setDashboard(refreshed);
      const refreshedRooms = await schoolApi<{ items: BoardingRoom[]; total: number }>("/api/v1/boarding/rooms");
      setRooms(refreshedRooms.items);
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to assign the student.",
        tone: "error",
      });
    } finally {
      setAssignmentPending(false);
    }
  }

  if (loading) {
    return <div className="workspace-loading">Loading boarding workspace…</div>;
  }

  return (
    <div className="workspace-shell">
      {notice && (
        <FeedbackModal
          message={notice.message}
          tone={notice.tone}
          onClose={() => setNotice(null)}
        />
      )}

      <div className="workspace-header-row">
        <div className="workspace-header-copy">
          <span className="workspace-kicker">Residential management</span>
          <h2>Boarding & hostel operations</h2>
        </div>
        <div className="workspace-header-actions">
          <button className="button button-secondary" onClick={() => void loadWorkspace(true)}>
            <RefreshCw size={16} className={refreshing ? "spin" : ""} /> Refresh
          </button>
          <button className="button button-primary" onClick={() => setRoomModalOpen(true)}>
            <Plus size={17} /> Add room
          </button>
        </div>
      </div>

      <section className="stat-grid">
        <article className="stat-card">
          <div className="stat-heading"><span>Total rooms</span><span className="stat-icon violet-icon"><Home size={17} /></span></div>
          <div className="stat-value">{dashboard?.total_rooms ?? 0}</div>
          <div className="stat-foot"><span className="trend-positive">{dashboard?.occupied_rooms ?? 0} occupied</span><span>of {dashboard?.total_capacity ?? 0} seats</span></div>
        </article>
        <article className="stat-card">
          <div className="stat-heading"><span>Available slots</span><span className="stat-icon green-icon"><BedDouble size={17} /></span></div>
          <div className="stat-value">{dashboard?.available_slots ?? 0}</div>
          <div className="stat-foot"><span>Across all hostels</span><span className="stat-foot-right">Capacity-managed</span></div>
        </article>
        <article className="stat-card">
          <div className="stat-heading"><span>Active residents</span><span className="stat-icon orange-icon"><Users size={17} /></span></div>
          <div className="stat-value">{dashboard?.active_residents ?? 0}</div>
          <div className="stat-foot"><span className="trend-positive">{dashboard?.occupied_rooms ?? 0} rooms in use</span><span>resident roster</span></div>
        </article>
        <article className="stat-card">
          <div className="stat-heading"><span>Monthly revenue</span><span className="stat-icon blue-icon"><Coins size={17} /></span></div>
          <div className="stat-value">{formatMoney(dashboard?.monthly_revenue ?? 0)}</div>
          <div className="stat-foot"><span>Current active boarding fees</span><span className="stat-foot-right">Boarding fees</span></div>
        </article>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>Boarding rooms</h3>
            <p>Track capacity, occupancy, and room status for residential housing.</p>
          </div>
          <div className="search-box">
            <Search size={15} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search rooms" aria-label="Search rooms" />
          </div>
        </div>

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Room</th>
                <th>Type</th>
                <th>Capacity</th>
                <th>Occupied</th>
                <th>Available</th>
                <th>Monthly fee</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {filteredRooms.map((room) => (
                <tr key={room.id}>
                  <td><strong>{room.name}</strong></td>
                  <td>{room.room_type}</td>
                  <td>{room.capacity}</td>
                  <td>{room.occupied_count}</td>
                  <td>{room.available_slots}</td>
                  <td>{formatMoney(room.monthly_fee)}</td>
                  <td><span className={`tag ${room.status === "available" ? "tag-green" : room.status === "full" ? "tag-orange" : "tag-gray"}`}>{room.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>Resident assignments</h3>
            <p>Manage who is living in each room and their check-in dates.</p>
          </div>
          <button className="button button-primary" onClick={() => setAssignmentModalOpen(true)}>
            <Plus size={16} /> Assign student
          </button>
        </div>

        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Student</th>
                <th>Room</th>
                <th>Bed</th>
                <th>Check-in</th>
                <th>Fee</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {assignments.map((assignment) => (
                <tr key={assignment.id}>
                  <td>{assignment.student_name}</td>
                  <td>{assignment.room_name}</td>
                  <td>{assignment.bed_number ?? "—"}</td>
                  <td>{formatDate(assignment.check_in_date)}</td>
                  <td>{formatMoney(assignment.room_fee)}</td>
                  <td><span className={`tag ${assignment.status === "active" ? "tag-green" : assignment.status === "pending" ? "tag-orange" : "tag-gray"}`}>{assignment.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {roomModalOpen && (
        <div className="feedback-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setRoomModalOpen(false)}>
          <section className="feedback-modal" role="dialog" aria-modal="true">
            <div className="feedback-modal-icon"><Home size={22} /></div>
            <button className="feedback-modal-close" type="button" aria-label="Close" onClick={() => setRoomModalOpen(false)}>×</button>
            <h2>Add boarding room</h2>
            <form onSubmit={submitRoom} className="workspace-form">
              <div className="form-grid">
                <label><span>Name</span><input value={roomForm.name} onChange={(event) => setRoomForm({ ...roomForm, name: event.target.value })} required /></label>
                <label><span>Room type</span><select value={roomForm.room_type} onChange={(event) => setRoomForm({ ...roomForm, room_type: event.target.value })}>
                  <option value="single">Single</option>
                  <option value="double">Double</option>
                  <option value="dormitory">Dormitory</option>
                </select></label>
                <label><span>Capacity</span><input type="number" min={1} max={40} value={roomForm.capacity} onChange={(event) => setRoomForm({ ...roomForm, capacity: event.target.value })} required /></label>
                <label><span>Monthly fee</span><input type="number" min={0} step="0.01" value={roomForm.monthly_fee} onChange={(event) => setRoomForm({ ...roomForm, monthly_fee: event.target.value })} required /></label>
                <label className="full-width"><span>Notes</span><textarea value={roomForm.notes} onChange={(event) => setRoomForm({ ...roomForm, notes: event.target.value })} rows={3} /></label>
              </div>
              <footer>
                <button type="button" className="feedback-modal-secondary" onClick={() => setRoomModalOpen(false)}>Cancel</button>
                <button type="submit" className="feedback-modal-primary" disabled={roomPending}>{roomPending ? "Saving…" : "Save room"}</button>
              </footer>
            </form>
          </section>
        </div>
      )}

      {assignmentModalOpen && (
        <div className="feedback-modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && setAssignmentModalOpen(false)}>
          <section className="feedback-modal" role="dialog" aria-modal="true">
            <div className="feedback-modal-icon"><BedDouble size={22} /></div>
            <button className="feedback-modal-close" type="button" aria-label="Close" onClick={() => setAssignmentModalOpen(false)}>×</button>
            <h2>Assign student to room</h2>
            <form onSubmit={submitAssignment} className="workspace-form">
              <div className="form-grid">
                <label><span>Room</span><select value={assignmentForm.room_id} onChange={(event) => setAssignmentForm({ ...assignmentForm, room_id: event.target.value })} required>
                  <option value="">Select room</option>
                  {rooms.map((room) => <option key={room.id} value={room.id}>{room.name}</option>)}
                </select></label>
                <label><span>Student</span><select value={assignmentForm.student_id} onChange={(event) => setAssignmentForm({ ...assignmentForm, student_id: event.target.value })} required>
                  <option value="">Select student</option>
                  {students.map((student) => <option key={student.id} value={student.id}>{student.first_name} {student.last_name}</option>)}
                </select></label>
                <label><span>Bed number</span><input value={assignmentForm.bed_number} onChange={(event) => setAssignmentForm({ ...assignmentForm, bed_number: event.target.value })} /></label>
                <label><span>Check-in date</span><input type="date" value={assignmentForm.check_in_date} onChange={(event) => setAssignmentForm({ ...assignmentForm, check_in_date: event.target.value })} required /></label>
                <label><span>Room fee</span><input type="number" min={0} step="0.01" value={assignmentForm.room_fee} onChange={(event) => setAssignmentForm({ ...assignmentForm, room_fee: event.target.value })} required /></label>
                <label className="full-width"><span>Notes</span><textarea value={assignmentForm.notes} onChange={(event) => setAssignmentForm({ ...assignmentForm, notes: event.target.value })} rows={3} /></label>
              </div>
              <footer>
                <button type="button" className="feedback-modal-secondary" onClick={() => setAssignmentModalOpen(false)}>Cancel</button>
                <button type="submit" className="feedback-modal-primary" disabled={assignmentPending}>{assignmentPending ? "Assigning…" : "Assign student"}</button>
              </footer>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
