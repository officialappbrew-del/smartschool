"use client";

import { useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  CalendarClock,
  CheckCheck,
  FileText,
  GraduationCap,
  Plus,
  RefreshCw,
} from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type Course = {
  id: string;
  title: string;
  description: string | null;
  class_section_id: string | null;
  class_name: string | null;
  teacher_name: string | null;
  status: "draft" | "published";
  created_at: string;
  updated_at: string;
};

type Assignment = {
  id: string;
  course_id: string;
  course_title: string;
  title: string;
  instructions: string | null;
  due_date: string | null;
  max_score: number | string | null;
  status: "draft" | "published" | "closed";
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
  submission_count: number;
};

type ClassOption = {
  id: string;
  name: string;
  academic_year: string;
};

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatScore(value: number | string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(2) : String(value);
}

export default function LearningWorkspace() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [classes, setClasses] = useState<ClassOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ message: string; tone: "error" | "success" | "info" } | null>(null);
  const [courseForm, setCourseForm] = useState({
    title: "",
    description: "",
    class_section_id: "",
    status: "draft" as "draft" | "published",
  });
  const [assignmentForm, setAssignmentForm] = useState({
    course_id: "",
    title: "",
    instructions: "",
    due_date: "",
    max_score: "",
    status: "draft" as "draft" | "published" | "closed",
  });

  const summaryCards = useMemo(
    () => [
      { label: "Courses", value: String(courses.length), accent: "purple", icon: BookOpen },
      { label: "Assignments", value: String(assignments.length), accent: "blue", icon: FileText },
      { label: "Published", value: String(courses.filter((course) => course.status === "published").length), accent: "green", icon: CheckCheck },
      { label: "Open submissions", value: String(assignments.reduce((total, item) => total + item.submission_count, 0)), accent: "orange", icon: CalendarClock },
    ],
    [courses, assignments],
  );

  async function refreshWorkspace(isRefresh = false) {
    if (isRefresh) setRefreshing(true);
    try {
      const [courseData, assignmentData, classData] = await Promise.all([
        schoolApi<Course[]>("/api/v1/lms/courses"),
        schoolApi<Assignment[]>("/api/v1/lms/assignments"),
        schoolApi<{ items: ClassOption[]; total: number }>("/api/v1/classes?offset=0&limit=250"),
      ]);
      setCourses(courseData ?? []);
      setAssignments(assignmentData ?? []);
      setClasses(classData.items ?? []);
      setAssignmentForm((current) => ({
        ...current,
        course_id: current.course_id || courseData[0]?.id || "",
      }));
    } finally {
      if (isRefresh) setRefreshing(false);
    }
  }

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        await refreshWorkspace();
      } catch (error: unknown) {
        if (!active) return;
        setNotice({
          message: error instanceof Error ? error.message : "Unable to load learning data.",
          tone: "error",
        });
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  async function createCourse(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      await schoolApi<Course>("/api/v1/lms/courses", {
        method: "POST",
        body: JSON.stringify({
          title: courseForm.title.trim(),
          description: courseForm.description.trim() || null,
          class_section_id: courseForm.class_section_id || null,
          status: courseForm.status,
        }),
      });
      setCourseForm({ title: "", description: "", class_section_id: "", status: "draft" });
      await refreshWorkspace();
      setNotice({ message: "Learning course created successfully.", tone: "success" });
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to create this course.",
        tone: "error",
      });
    } finally {
      setSaving(false);
    }
  }

  async function createAssignment(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      await schoolApi<Assignment>("/api/v1/lms/assignments", {
        method: "POST",
        body: JSON.stringify({
          course_id: assignmentForm.course_id,
          title: assignmentForm.title.trim(),
          instructions: assignmentForm.instructions.trim() || null,
          due_date: assignmentForm.due_date || null,
          max_score: assignmentForm.max_score ? Number(assignmentForm.max_score) : null,
          status: assignmentForm.status,
        }),
      });
      setAssignmentForm({
        course_id: "",
        title: "",
        instructions: "",
        due_date: "",
        max_score: "",
        status: "draft",
      });
      await refreshWorkspace();
      setNotice({ message: "Assignment published to the course.", tone: "success" });
    } catch (error: unknown) {
      setNotice({
        message: error instanceof Error ? error.message : "Unable to create this assignment.",
        tone: "error",
      });
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return (
      <section className="learning-loading" role="status">
        <span className="learning-loading-icon"><GraduationCap size={22} /></span>
        <strong>Preparing your learning workspace</strong>
        <span>Loading courses, assignments, and class information…</span>
      </section>
    );
  }

  return (
    <div className="learning-workspace">
      {notice ? (
        <FeedbackModal
          tone={notice.tone}
          title={notice.tone === "success" ? "Learning update" : notice.tone === "error" ? "Learning issue" : "Learning notice"}
          message={notice.message}
          onClose={() => setNotice(null)}
        />
      ) : null}

      <header className="learning-hero">
        <div className="learning-hero-copy">
          <span className="learning-hero-icon"><GraduationCap size={22} /></span>
          <div>
            <p className="learning-eyebrow">ACADEMIC DELIVERY</p>
            <h2>Learning management</h2>
            <p>Organize courses, share assignments, and follow learning activity across your school.</p>
          </div>
        </div>
        <div className="learning-hero-actions">
          <button
            className="button button-secondary"
            type="button"
            onClick={() => {
              void refreshWorkspace(true).catch((error: unknown) => {
                setNotice({
                  message: error instanceof Error ? error.message : "Unable to refresh learning data.",
                  tone: "error",
                });
              });
            }}
            disabled={saving || refreshing}
          >
            <RefreshCw size={14} className={refreshing ? "learning-spin" : ""} />
            Refresh
          </button>
        </div>
      </header>

      <section className="reports-stat-grid" aria-label="Learning summary">
        {summaryCards.map((card) => (
          <article key={card.label} className={`panel learning-stat-card learning-${card.accent}`}>
            <span className="learning-stat-icon"><card.icon size={17} /></span>
            <div className="learning-stat-copy">
              <span>{card.label}</span>
              <strong>{card.value}</strong>
            </div>
          </article>
        ))}
      </section>

      <section className="learning-forms">
        <form onSubmit={createCourse} className="panel learning-form">
          <div className="learning-form-heading">
            <span className="learning-form-icon learning-form-icon-purple"><BookOpen size={18} /></span>
            <div><h3>Create a course</h3><p>Set up a subject or learning programme for your students.</p></div>
          </div>
          <label>
            <span>Course title <i>Required</i></span>
            <input value={courseForm.title} onChange={(event) => setCourseForm((current) => ({ ...current, title: event.target.value }))} placeholder="e.g. Biology Foundation" required />
          </label>
          <label>
            <span>Description <small>Optional</small></span>
            <textarea value={courseForm.description} onChange={(event) => setCourseForm((current) => ({ ...current, description: event.target.value }))} placeholder="Outline the course objectives and what students will learn." rows={3} />
          </label>
          <label>
            <span>Class section <small>Optional</small></span>
            <select value={courseForm.class_section_id} onChange={(event) => setCourseForm((current) => ({ ...current, class_section_id: event.target.value }))}>
              <option value="">No specific class</option>
              {classes.map((classItem) => (
                <option key={classItem.id} value={classItem.id}>{classItem.name} · {classItem.academic_year}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Course visibility</span>
            <select value={courseForm.status} onChange={(event) => setCourseForm((current) => ({ ...current, status: event.target.value as "draft" | "published" }))}>
              <option value="draft">Draft</option>
              <option value="published">Published</option>
            </select>
          </label>
          <button className="button button-primary learning-submit" type="submit" disabled={saving || !courseForm.title.trim()}>
            <Plus size={15} /> {saving ? "Creating course…" : "Create course"}
          </button>
        </form>

        <form onSubmit={createAssignment} className="panel learning-form">
          <div className="learning-form-heading">
            <span className="learning-form-icon learning-form-icon-blue"><FileText size={18} /></span>
            <div><h3>Create an assignment</h3><p>Give students clear instructions and a due date.</p></div>
          </div>
          <label>
            <span>Course <i>Required</i></span>
            <select value={assignmentForm.course_id} onChange={(event) => setAssignmentForm((current) => ({ ...current, course_id: event.target.value }))} required>
              <option value="">{courses.length ? "Select a course" : "Create a course first"}</option>
              {courses.map((course) => (
                <option key={course.id} value={course.id}>{course.title}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Assignment title <i>Required</i></span>
            <input value={assignmentForm.title} onChange={(event) => setAssignmentForm((current) => ({ ...current, title: event.target.value }))} placeholder="Research task" required />
          </label>
          <label>
            <span>Instructions <small>Optional</small></span>
            <textarea value={assignmentForm.instructions} onChange={(event) => setAssignmentForm((current) => ({ ...current, instructions: event.target.value }))} placeholder="Explain the task, expectations, and how students should submit their work." rows={3} />
          </label>
          <div className="learning-inline-fields">
            <label>
              <span>Due date <small>Optional</small></span>
              <input type="date" value={assignmentForm.due_date} onChange={(event) => setAssignmentForm((current) => ({ ...current, due_date: event.target.value }))} />
            </label>
            <label>
              <span>Maximum score <small>Optional</small></span>
              <input type="number" step="0.01" min="0" value={assignmentForm.max_score} onChange={(event) => setAssignmentForm((current) => ({ ...current, max_score: event.target.value }))} />
            </label>
          </div>
          <label>
            <span>Assignment visibility</span>
            <select value={assignmentForm.status} onChange={(event) => setAssignmentForm((current) => ({ ...current, status: event.target.value as "draft" | "published" | "closed" }))}>
              <option value="draft">Draft</option>
              <option value="published">Published</option>
              <option value="closed">Closed</option>
            </select>
          </label>
          <button className="button button-primary learning-submit" type="submit" disabled={saving || !assignmentForm.course_id || !assignmentForm.title.trim()}>
            <Plus size={15} /> {saving ? "Creating assignment…" : "Create assignment"}
          </button>
        </form>
      </section>

      <section className="panel learning-list-panel">
        <header className="learning-list-heading">
          <div className="learning-list-title">
            <span className="learning-list-icon learning-form-icon-purple"><CheckCheck size={17} /></span>
            <div><h3>Courses</h3><p>Manage your school's course catalogue.</p></div>
          </div>
          <span className="learning-count">{courses.length} {courses.length === 1 ? "course" : "courses"}</span>
        </header>
        <div className="learning-table-wrap">
          <table className="learning-table">
            <thead>
              <tr>
                <th>Course</th>
                <th>Class</th>
                <th>Teacher</th>
                <th>Status</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {courses.length === 0 ? (
                <tr><td colSpan={5}>
                  <div className="learning-empty">
                    <span><BookOpen size={20} /></span>
                    <strong>Your course catalogue is ready</strong>
                    <p>Create a course above to organize lessons and assignments for your students.</p>
                  </div>
                </td></tr>
              ) : (
                courses.map((course) => (
                  <tr key={course.id}>
                    <td>
                      <strong>{course.title}</strong>
                      {course.description ? <small>{course.description}</small> : null}
                    </td>
                    <td>{course.class_name ?? "General"}</td>
                    <td>{course.teacher_name ?? "Unassigned"}</td>
                    <td><span className={`learning-status ${course.status}`}>{course.status}</span></td>
                    <td>{formatDate(course.updated_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel learning-list-panel">
        <header className="learning-list-heading">
          <div className="learning-list-title">
            <span className="learning-list-icon learning-form-icon-blue"><CalendarClock size={17} /></span>
            <div><h3>Assignments</h3><p>Track student work and upcoming due dates.</p></div>
          </div>
          <span className="learning-count">{assignments.length} {assignments.length === 1 ? "assignment" : "assignments"}</span>
        </header>
        <div className="learning-table-wrap">
          <table className="learning-table">
            <thead>
              <tr>
                <th>Course</th>
                <th>Assignment</th>
                <th>Due</th>
                <th>Score</th>
                <th>Submissions</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {assignments.length === 0 ? (
                <tr><td colSpan={6}>
                  <div className="learning-empty">
                    <span><FileText size={20} /></span>
                    <strong>No assignments yet</strong>
                    <p>Once you create an assignment, its course, deadline, and submission progress will appear here.</p>
                  </div>
                </td></tr>
              ) : (
                assignments.map((assignment) => (
                  <tr key={assignment.id}>
                    <td>{assignment.course_title}</td>
                    <td>
                      <strong>{assignment.title}</strong>
                      {assignment.instructions ? <small>{assignment.instructions}</small> : null}
                    </td>
                    <td>{formatDate(assignment.due_date)}</td>
                    <td>{formatScore(assignment.max_score)}</td>
                    <td>{assignment.submission_count}</td>
                    <td><span className={`learning-status ${assignment.status}`}>{assignment.status}</span></td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
