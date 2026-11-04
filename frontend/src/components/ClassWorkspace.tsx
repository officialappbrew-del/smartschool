"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ArrowLeft, BookOpen, ClipboardCheck, FileText, GraduationCap, Mail, Plus, Save } from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type ClassSection = {
  id: string;
  name: string;
  academic_year: string;
  is_active: boolean;
  student_count: number;
};

type AcademicYear = {
  id: string;
  name: string;
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  class_count: number;
};

type AcademicTerm = {
  id: string;
  academic_year_id: string;
  academic_year: string;
  name: string;
  start_date: string;
  end_date: string;
};

type Account = {
  email: string;
};

type Teacher = {
  id: string;
  email: string;
  is_active: boolean;
  class_section_ids: string[];
};

export default function ClassWorkspace() {
  const [account, setAccount] = useState<Account | null>(null);
  const [classes, setClasses] = useState<ClassSection[]>([]);
  const [academicYears, setAcademicYears] = useState<AcademicYear[]>([]);
  const [termYearId, setTermYearId] = useState("");
  const [terms, setTerms] = useState<AcademicTerm[]>([]);
  const [termName, setTermName] = useState("");
  const [termStart, setTermStart] = useState("");
  const [termEnd, setTermEnd] = useState("");
  const [teachers, setTeachers] = useState<Teacher[]>([]);
  const [teacherAssignments, setTeacherAssignments] = useState<Record<string, string[]>>({});
  const [teacherEmail, setTeacherEmail] = useState("");
  const [teacherClassIds, setTeacherClassIds] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [academicYear, setAcademicYear] = useState("");
  const [yearName, setYearName] = useState("");
  const [yearStart, setYearStart] = useState("");
  const [yearEnd, setYearEnd] = useState("");
  const [yearIsCurrent, setYearIsCurrent] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingYear, setSavingYear] = useState(false);
  const [savingTerm, setSavingTerm] = useState(false);
  const [savingTeacher, setSavingTeacher] = useState(false);
  const [savingTeacherId, setSavingTeacherId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    async function load() {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      try {
        const [currentAccount, classSections, years, schoolTeachers] = await Promise.all([
          schoolApi<Account>("/api/v1/auth/me"),
          schoolApi<ClassSection[]>("/api/v1/classes"),
          schoolApi<AcademicYear[]>("/api/v1/academic-years"),
          schoolApi<Teacher[]>("/api/v1/teachers"),
        ]);
        if (active) {
          setAccount(currentAccount);
          setClasses(classSections);
          setAcademicYears(years);
          setTermYearId(
            years.find((year) => year.is_current)?.id ?? years[0]?.id ?? "",
          );
          setTeachers(schoolTeachers);
          setTeacherAssignments(Object.fromEntries(
            schoolTeachers.map((teacher) => [teacher.id, teacher.class_section_ids]),
          ));
          setAcademicYear(years.find((year) => year.is_current)?.name ?? years[0]?.name ?? "");
          setYearName(new Date().getFullYear() + "-" + (new Date().getFullYear() + 1));
        }
      } catch (loadError: unknown) {
        if (!active) return;
        if (!window.sessionStorage.getItem("northstar_access_token")) {
          window.location.replace("/login");
          return;
        }
        setError(loadError instanceof Error ? loadError.message : "Unable to load classes.");
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    async function loadTerms() {
      if (!termYearId) {
        setTerms([]);
        return;
      }
      try {
        const items = await schoolApi<AcademicTerm[]>(
          `/api/v1/academic-years/${encodeURIComponent(termYearId)}/terms`,
        );
        if (active) setTerms(items);
      } catch (loadError: unknown) {
        if (active) {
          setError(loadError instanceof Error ? loadError.message : "Unable to load academic terms.");
        }
      }
    }
    void loadTerms();
    return () => {
      active = false;
    };
  }, [termYearId]);

  async function createClass(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const newClass = await schoolApi<ClassSection>("/api/v1/classes", {
        method: "POST",
        body: JSON.stringify({ name, academic_year: academicYear }),
      });
      setClasses((current) =>
        [...current, newClass].sort(
          (left, right) =>
            right.academic_year.localeCompare(left.academic_year) ||
            left.name.localeCompare(right.name),
        ),
      );
      setAcademicYears((current) =>
        current.map((year) =>
          year.name === newClass.academic_year
            ? { ...year, class_count: year.class_count + 1 }
            : year,
        ),
      );
      setName("");
      setNotice(`${newClass.name} was added for ${newClass.academic_year}.`);
    } catch (createError: unknown) {
      setError(createError instanceof Error ? createError.message : "Class creation failed.");
    } finally {
      setSaving(false);
    }
  }

  async function createAcademicYear(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingYear(true);
    setError("");
    try {
      const newYear = await schoolApi<AcademicYear>("/api/v1/academic-years", {
        method: "POST",
        body: JSON.stringify({
          name: yearName,
          start_date: yearStart || null,
          end_date: yearEnd || null,
          is_current: yearIsCurrent,
        }),
      });
      setAcademicYears((current) => [
        ...current.map((year) => ({
          ...year,
          is_current: yearIsCurrent ? false : year.is_current,
        })),
        newYear,
      ].sort((left, right) => right.name.localeCompare(left.name)));
      setTermYearId(newYear.id);
      setAcademicYear(newYear.name);
      setYearName("");
      setYearStart("");
      setYearEnd("");
      setYearIsCurrent(false);
      setNotice(`${newYear.name} was added.`);
    } catch (createError: unknown) {
      setError(createError instanceof Error ? createError.message : "Academic year creation failed.");
    } finally {
      setSavingYear(false);
    }
  }

  async function createAcademicTerm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!termYearId) return;
    setSavingTerm(true);
    setError("");
    setNotice("");
    try {
      const created = await schoolApi<AcademicTerm>(
        `/api/v1/academic-years/${encodeURIComponent(termYearId)}/terms`,
        {
          method: "POST",
          body: JSON.stringify({
            name: termName,
            start_date: termStart,
            end_date: termEnd,
          }),
        },
      );
      setTerms((current) => [...current, created].sort(
        (left, right) => left.start_date.localeCompare(right.start_date),
      ));
      setTermName("");
      setTermStart("");
      setTermEnd("");
      setNotice(`${created.name} was added for ${created.academic_year}.`);
    } catch (createError: unknown) {
      setError(createError instanceof Error ? createError.message : "Academic term creation failed.");
    } finally {
      setSavingTerm(false);
    }
  }

  async function inviteTeacher(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSavingTeacher(true);
    setError("");
    setNotice("");
    try {
      const teacher = await schoolApi<Teacher>("/api/v1/teachers/invitations", {
        method: "POST",
        body: JSON.stringify({
          email: teacherEmail,
          class_section_ids: teacherClassIds,
        }),
      });
      setTeachers((current) => [...current, teacher].sort((a, b) => a.email.localeCompare(b.email)));
      setTeacherAssignments((current) => ({ ...current, [teacher.id]: teacher.class_section_ids }));
      setTeacherEmail("");
      setTeacherClassIds([]);
      setNotice(`Teacher invitation sent to ${teacher.email}.`);
    } catch (inviteError: unknown) {
      setError(inviteError instanceof Error ? inviteError.message : "Teacher invitation failed.");
    } finally {
      setSavingTeacher(false);
    }
  }

  async function saveTeacherAssignments(teacher: Teacher) {
    setSavingTeacherId(teacher.id);
    setError("");
    setNotice("");
    try {
      const updated = await schoolApi<Teacher>(
        `/api/v1/teachers/${encodeURIComponent(teacher.id)}/classes`,
        {
          method: "PUT",
          body: JSON.stringify({
            class_section_ids: teacherAssignments[teacher.id] ?? [],
          }),
        },
      );
      setTeachers((current) => current.map((item) => item.id === teacher.id ? updated : item));
      setTeacherAssignments((current) => ({
        ...current,
        [teacher.id]: updated.class_section_ids,
      }));
      setNotice(`Class assignments updated for ${teacher.email}.`);
    } catch (saveError: unknown) {
      setError(saveError instanceof Error ? saveError.message : "Teacher assignments could not be saved.");
    } finally {
      setSavingTeacherId("");
    }
  }

  if (loading) {
    return <main className="workspace-loading" aria-live="polite">Loading school classes…</main>;
  }

  const schoolName = typeof window === "undefined"
    ? ""
    : window.location.hostname.split(".")[0]
      .replace(/-/g, " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());

  return (
    <div className="student-workspace">
      <aside className="student-sidebar">
        <Link className="brand student-brand" href="/">
          <span className="brand-mark"><GraduationCap size={21} /></span>
          <span><span className="brand-name">northstar</span><span className="brand-caption">SCHOOL MANAGEMENT</span></span>
        </Link>
        <div className="student-school-name">{schoolName || "School workspace"}</div>
        <div className="nav-label">WORKSPACE</div>
        <Link className="student-side-link" href="/"><ArrowLeft size={17} /> Overview</Link>
        <Link className="student-side-link" href="/students"><GraduationCap size={17} /> Students</Link>
        <Link className="student-side-link" href="/attendance"><ClipboardCheck size={17} /> Attendance</Link>
        <Link className="student-side-link" href="/grades"><BookOpen size={17} /> Grades</Link>
        <Link className="student-side-link" href="/report-cards"><FileText size={17} /> Report cards</Link>
        <div className="student-side-link active"><GraduationCap size={17} /> Classes</div>
        <div className="student-account">
          <span className="student-account-avatar">{account?.email[0]?.toUpperCase() ?? "A"}</span>
          <span><strong>{account?.email}</strong><small>School administrator</small></span>
        </div>
      </aside>

      <main className="student-main">
        <header className="student-topbar">
          <div><span>Workspace</span><b>/</b><strong>Classes</strong></div>
          <button className="student-signout" onClick={() => {
            window.sessionStorage.removeItem("northstar_access_token");
            window.location.replace("/login");
          }}>Sign out</button>
        </header>

        <div className="student-page-content">
          <section className="student-page-heading">
            <div>
              <p className="eyebrow">ACADEMIC STRUCTURE</p>
              <h1>Classes</h1>
              <p className="page-description">Set up class sections by academic year and track current enrollment.</p>
            </div>
            <Link className="button button-secondary" href="/students">View students</Link>
          </section>

          <section className="panel academic-year-panel">
            <div className="student-records-toolbar">
              <div><h2>Academic years</h2><p>Add a school year with optional dates. Only one year can be marked current.</p></div>
            </div>
            <form className="academic-year-form" onSubmit={createAcademicYear}>
              <label>Academic year<input required minLength={4} maxLength={32} value={yearName} onChange={(event) => setYearName(event.target.value)} placeholder="2026-2027" /></label>
              <label>Start date<input type="date" value={yearStart} onChange={(event) => setYearStart(event.target.value)} /></label>
              <label>End date<input type="date" value={yearEnd} onChange={(event) => setYearEnd(event.target.value)} /></label>
              <label className="current-year-checkbox"><input type="checkbox" checked={yearIsCurrent} onChange={(event) => setYearIsCurrent(event.target.checked)} /> Current year</label>
              <button className="button button-primary" type="submit" disabled={savingYear}><Plus size={15} />{savingYear ? "Adding…" : "Add year"}</button>
            </form>
            {academicYears.length > 0 && (
              <div className="academic-year-list">
                {academicYears.map((year) => (
                  <span key={year.id} className="academic-year-chip">
                    <strong>{year.name}</strong>
                    {year.is_current && <small>Current</small>}
                    <span>{year.class_count} classes</span>
                  </span>
                ))}
              </div>
            )}
          </section>

          <section className="panel term-management-panel">
            <div className="student-records-toolbar">
              <div><h2>Academic terms</h2><p>Define non-overlapping terms for each academic year.</p></div>
            </div>
            <form className="term-create-form" onSubmit={(event) => void createAcademicTerm(event)}>
              <label>Academic year
                <select required value={termYearId} onChange={(event) => setTermYearId(event.target.value)} disabled={academicYears.length === 0}>
                  {academicYears.length === 0 && <option value="">Create an academic year first</option>}
                  {academicYears.map((year) => <option key={year.id} value={year.id}>{year.name}{year.is_current ? " (Current)" : ""}</option>)}
                </select>
              </label>
              <label>Term name<input required maxLength={80} value={termName} onChange={(event) => setTermName(event.target.value)} placeholder="Term 1" /></label>
              <label>Start date<input required type="date" value={termStart} onChange={(event) => setTermStart(event.target.value)} /></label>
              <label>End date<input required type="date" min={termStart || undefined} value={termEnd} onChange={(event) => setTermEnd(event.target.value)} /></label>
              <button className="button button-primary" type="submit" disabled={!termYearId || savingTerm}>
                <Plus size={15} />{savingTerm ? "Adding…" : "Add term"}
              </button>
            </form>
            {terms.length > 0 && (
              <div className="academic-year-list term-list">
                {terms.map((term) => (
                  <span key={term.id} className="academic-year-chip">
                    <strong>{term.name}</strong>
                    <span>{term.start_date} – {term.end_date}</span>
                  </span>
                ))}
              </div>
            )}
          </section>

          <section className="panel class-management-panel">
            <div className="student-records-toolbar">
              <div><h2>Add a class section</h2><p>Class names are unique within each academic year.</p></div>
            </div>
            <form className="class-create-form" onSubmit={createClass}>
              <label>Class or section name<input required maxLength={80} value={name} onChange={(event) => setName(event.target.value)} placeholder="Grade 5A" /></label>
              <label>Academic year<select required value={academicYear} onChange={(event) => setAcademicYear(event.target.value)}><option value="" disabled>Select year</option>{academicYears.map((year) => <option key={year.id} value={year.name}>{year.name}{year.is_current ? " (Current)" : ""}</option>)}</select></label>
              <button className="button button-primary" type="submit" disabled={saving || academicYears.length === 0}><Plus size={15} />{saving ? "Adding…" : "Add class"}</button>
            </form>
          </section>

          <section className="panel teacher-management-panel">
            <div className="student-records-toolbar">
              <div><h2>Teachers and class access</h2><p>Invite a teacher by email and restrict their access to assigned classes.</p></div>
            </div>
            <form className="teacher-invite-form" onSubmit={(event) => void inviteTeacher(event)}>
              <label>Teacher email<input type="email" required maxLength={254} value={teacherEmail} onChange={(event) => setTeacherEmail(event.target.value)} placeholder="teacher@example.com" /></label>
              <fieldset>
                <legend>Assigned classes</legend>
                {classes.map((classSection) => (
                  <label key={classSection.id}>
                    <input
                      type="checkbox"
                      checked={teacherClassIds.includes(classSection.id)}
                      onChange={(event) => setTeacherClassIds((current) =>
                        event.target.checked
                          ? [...current, classSection.id]
                          : current.filter((id) => id !== classSection.id),
                      )}
                    />
                    {classSection.name} · {classSection.academic_year}
                  </label>
                ))}
                {classes.length === 0 && <span className="teacher-empty">Create a class section before inviting teachers.</span>}
              </fieldset>
              <button className="button button-primary" type="submit" disabled={savingTeacher || classes.length === 0 || teacherClassIds.length === 0}>
                <Mail size={15} />{savingTeacher ? "Sending…" : "Invite teacher"}
              </button>
            </form>
            {teachers.length > 0 && (
              <div className="teacher-list">
                {teachers.map((teacher) => (
                  <section className="teacher-row" key={teacher.id}>
                    <div className="teacher-heading">
                      <strong>{teacher.email}</strong>
                      <span className={`teacher-account-state${teacher.is_active ? " active" : ""}`}>
                        {teacher.is_active ? "Active" : "Invitation pending"}
                      </span>
                    </div>
                    <fieldset>
                      <legend>Assigned classes</legend>
                      {classes.map((classSection) => (
                        <label key={classSection.id}>
                          <input
                            type="checkbox"
                            checked={(teacherAssignments[teacher.id] ?? []).includes(classSection.id)}
                            onChange={(event) => setTeacherAssignments((current) => {
                              const assigned = current[teacher.id] ?? [];
                              return {
                                ...current,
                                [teacher.id]: event.target.checked
                                  ? [...assigned, classSection.id]
                                  : assigned.filter((id) => id !== classSection.id),
                              };
                            })}
                          />
                          {classSection.name} · {classSection.academic_year}
                        </label>
                      ))}
                    </fieldset>
                    <button className="button button-secondary" disabled={savingTeacherId === teacher.id} onClick={() => void saveTeacherAssignments(teacher)}>
                      <Save size={14} />{savingTeacherId === teacher.id ? "Saving…" : "Save assignments"}
                    </button>
                  </section>
                ))}
              </div>
            )}
          </section>

          <section className="panel student-records-panel class-list-panel">
            <div className="student-records-toolbar">
              <div><h2>Class sections</h2><p>{classes.length} class section{classes.length === 1 ? "" : "s"} configured</p></div>
            </div>
            <div className="table-scroll">
              <table className="student-record-table">
                <thead><tr><th>CLASS / SECTION</th><th>ACADEMIC YEAR</th><th>ENROLLED STUDENTS</th></tr></thead>
                <tbody>
                  {classes.map((classSection) => (
                    <tr key={classSection.id}>
                      <td><strong className="class-table-name">{classSection.name}</strong></td>
                      <td>{classSection.academic_year}</td>
                      <td>{classSection.student_count}</td>
                    </tr>
                  ))}
                  {classes.length === 0 && <tr><td colSpan={3} className="empty-row">No classes yet. Add a class section to start enrolling students.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
          <footer className="student-page-footer">Students admitted with a class and academic year are enrolled automatically. Class changes retain prior enrollment history.</footer>
        </div>
      </main>
      <FeedbackModal
        message={error || notice}
        tone={error ? "error" : "success"}
        onClose={() => {
          setError("");
          setNotice("");
        }}
      />
    </div>
  );
}
