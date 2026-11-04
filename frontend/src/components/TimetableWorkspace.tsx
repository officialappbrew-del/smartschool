"use client";

import { useEffect, useMemo, useState } from "react";
import { CalendarDays, Clock3, MapPin, Pencil, Plus, Trash2 } from "lucide-react";
import type { FormEvent } from "react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type ClassSection = { id: string; name: string; is_active: boolean };
type Teacher = { id: string; email: string; is_active: boolean; class_section_ids: string[] };
type TimetableEntry = {
  id: string;
  class_section_id: string;
  class_name: string;
  teacher_id: string;
  teacher_email: string;
  subject: string;
  room: string | null;
  weekday: number;
  start_time: string;
  end_time: string;
};

const weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const emptyForm = {
  class_section_id: "",
  teacher_id: "",
  subject: "",
  room: "",
  weekday: "0",
  start_time: "09:00",
  end_time: "10:00",
};

function timeLabel(value: string) {
  return value.slice(0, 5);
}

export default function TimetableWorkspace() {
  const [classes, setClasses] = useState<ClassSection[]>([]);
  const [teachers, setTeachers] = useState<Teacher[]>([]);
  const [entries, setEntries] = useState<TimetableEntry[]>([]);
  const [form, setForm] = useState(emptyForm);
  const [editingId, setEditingId] = useState("");
  const [deletingId, setDeletingId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [noticeTone, setNoticeTone] = useState<"error" | "success" | "info" | "confirm">("info");

  async function loadData() {
    const [classList, teacherList, timetable] = await Promise.all([
      schoolApi<ClassSection[]>("/api/v1/classes"),
      schoolApi<Teacher[]>("/api/v1/teachers"),
      schoolApi<TimetableEntry[]>("/api/v1/timetable"),
    ]);
    setClasses(classList.filter((item) => item.is_active));
    setTeachers(teacherList.filter((item) => item.is_active));
    setEntries(timetable);
    setForm((current) => ({
      ...current,
      class_section_id: current.class_section_id || classList.find((item) => item.is_active)?.id || "",
    }));
  }

  useEffect(() => {
    let active = true;
    if (!window.sessionStorage.getItem("northstar_access_token")) {
      window.location.replace("/login");
      return () => { active = false; };
    }
    loadData()
      .catch((error: unknown) => {
        if (active) setNotice(error instanceof Error ? error.message : "Unable to load the timetable.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, []);

  const assignedTeachers = useMemo(
    () => teachers.filter((teacher) => teacher.class_section_ids.includes(form.class_section_id)),
    [form.class_section_id, teachers],
  );
  const groupedEntries = useMemo(
    () => weekdays.map((name, weekday) => ({
      name,
      entries: entries.filter((entry) => entry.weekday === weekday)
        .sort((left, right) => left.start_time.localeCompare(right.start_time)),
    })),
    [entries],
  );

  function startEdit(entry: TimetableEntry) {
    setEditingId(entry.id);
    setForm({
      class_section_id: entry.class_section_id,
      teacher_id: entry.teacher_id,
      subject: entry.subject,
      room: entry.room ?? "",
      weekday: String(entry.weekday),
      start_time: timeLabel(entry.start_time),
      end_time: timeLabel(entry.end_time),
    });
  }

  function resetForm() {
    setEditingId("");
    setForm({ ...emptyForm, class_section_id: classes[0]?.id ?? "" });
  }

  async function saveEntry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const { room, weekday, ...rest } = form;
      const payload = {
        ...rest,
        room: room.trim() || null,
        weekday: Number(weekday),
      };
      const saved = await schoolApi<TimetableEntry>(
        editingId ? `/api/v1/timetable/${encodeURIComponent(editingId)}` : "/api/v1/timetable",
        { method: editingId ? "PUT" : "POST", body: JSON.stringify(payload) },
      );
      setEntries((current) => editingId
        ? current.map((entry) => entry.id === saved.id ? saved : entry)
        : [...current, saved]);
      resetForm();
      setNotice(editingId ? "Timetable entry updated." : "Timetable entry created.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to save the timetable entry.");
      setNoticeTone("error");
    } finally {
      setSaving(false);
    }
  }

  async function deleteEntry() {
    if (!deletingId) return;
    setSaving(true);
    try {
      await schoolApi<void>(`/api/v1/timetable/${encodeURIComponent(deletingId)}`, { method: "DELETE" });
      setEntries((current) => current.filter((entry) => entry.id !== deletingId));
      setDeletingId("");
      setNotice("Timetable entry deleted.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to delete the timetable entry.");
      setNoticeTone("error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="timetable-workspace">
      <section className="panel timetable-card">
        <header className="timetable-heading">
          <div>
            <p className="eyebrow">WEEKLY SCHEDULE</p>
            <h2>{editingId ? "Edit lesson" : "Add a lesson"}</h2>
            <p>Lessons repeat weekly. Overlapping classes, teachers, and rooms are blocked.</p>
          </div>
          <CalendarDays size={22} />
        </header>
        <form className="timetable-form" onSubmit={(event) => void saveEntry(event)}>
          <label>Class
            <select required value={form.class_section_id} onChange={(event) => setForm((current) => ({
              ...current, class_section_id: event.target.value, teacher_id: "",
            }))}>
              <option value="">Select a class</option>
              {classes.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label>Teacher
            <select required value={form.teacher_id} onChange={(event) => setForm((current) => ({ ...current, teacher_id: event.target.value }))}>
              <option value="">Select an assigned teacher</option>
              {assignedTeachers.map((teacher) => <option key={teacher.id} value={teacher.id}>{teacher.email}</option>)}
            </select>
          </label>
          <label>Subject
            <input required maxLength={100} value={form.subject} onChange={(event) => setForm((current) => ({ ...current, subject: event.target.value }))} placeholder="e.g. Mathematics" />
          </label>
          <label>Room
            <input maxLength={80} value={form.room} onChange={(event) => setForm((current) => ({ ...current, room: event.target.value }))} placeholder="e.g. Room 12" />
          </label>
          <label>Day
            <select value={form.weekday} onChange={(event) => setForm((current) => ({ ...current, weekday: event.target.value }))}>
              {weekdays.map((day, index) => <option key={day} value={index}>{day}</option>)}
            </select>
          </label>
          <label>Starts
            <input type="time" required value={form.start_time} onChange={(event) => setForm((current) => ({ ...current, start_time: event.target.value }))} />
          </label>
          <label>Ends
            <input type="time" required value={form.end_time} onChange={(event) => setForm((current) => ({ ...current, end_time: event.target.value }))} />
          </label>
          <div className="timetable-form-actions">
            {editingId && <button className="button button-secondary" type="button" onClick={resetForm}>Cancel edit</button>}
            <button className="button button-primary" type="submit" disabled={saving || !classes.length || !assignedTeachers.length}>
              {editingId ? <Pencil size={14} /> : <Plus size={15} />}
              {saving ? "Saving…" : editingId ? "Update lesson" : "Add lesson"}
            </button>
          </div>
        </form>
        {!loading && (!classes.length || !teachers.some((teacher) => teacher.is_active)) && (
          <p className="timetable-help">Create an active class and assign an active teacher before adding timetable lessons.</p>
        )}
      </section>

      <section className="panel timetable-card">
        <header className="timetable-heading">
          <div><p className="eyebrow">SCHEDULE</p><h2>Weekly timetable</h2><p>{entries.length} scheduled lesson{entries.length === 1 ? "" : "s"}</p></div>
        </header>
        {loading ? <p className="timetable-empty">Loading timetable…</p> : entries.length === 0 ? (
          <p className="timetable-empty">No lessons scheduled yet. Add the first lesson above.</p>
        ) : (
          <div className="timetable-week">
            {groupedEntries.filter((day) => day.entries.length > 0).map((day) => (
              <section className="timetable-day" key={day.name}>
                <h3>{day.name}</h3>
                <div className="timetable-day-list">
                  {day.entries.map((entry) => (
                    <article className="timetable-entry" key={entry.id}>
                      <div className="timetable-entry-time"><Clock3 size={13} /> {timeLabel(entry.start_time)}–{timeLabel(entry.end_time)}</div>
                      <div className="timetable-entry-info">
                        <strong>{entry.subject}</strong>
                        <span>{entry.class_name} · {entry.teacher_email}</span>
                      </div>
                      {entry.room && <span className="timetable-room"><MapPin size={12} /> {entry.room}</span>}
                      <div className="timetable-entry-actions">
                        <button className="icon-button" type="button" aria-label={`Edit ${entry.subject}`} onClick={() => startEdit(entry)}><Pencil size={14} /></button>
                        <button className="icon-button timetable-delete" type="button" aria-label={`Delete ${entry.subject}`} onClick={() => { setDeletingId(entry.id); setNoticeTone("confirm"); }}><Trash2 size={14} /></button>
                      </div>
                    </article>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </section>
      <FeedbackModal
        message={noticeTone === "confirm" ? "Delete this lesson from the weekly timetable?" : notice}
        tone={noticeTone}
        title={noticeTone === "confirm" ? "Delete timetable entry?" : undefined}
        confirmLabel="Delete lesson"
        confirming={saving}
        onClose={() => {
          if (noticeTone === "confirm") {
            setDeletingId("");
            setNoticeTone("info");
            setNotice("");
          } else setNotice("");
        }}
        onConfirm={noticeTone === "confirm" ? () => void deleteEntry() : undefined}
      />
    </div>
  );
}
