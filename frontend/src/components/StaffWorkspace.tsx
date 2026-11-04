"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowLeft,
  BriefcaseBusiness,
  GraduationCap,
  Pencil,
  Plus,
  Save,
  UserRound,
} from "lucide-react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type Teacher = {
  id: string;
  email: string;
  is_active: boolean;
  class_section_ids: string[];
};

type Qualification = {
  id: string;
  title: string;
  institution: string;
  field_of_study: string | null;
  awarded_on: string | null;
  notes: string | null;
};

type Contract = {
  id: string;
  contract_type: string;
  start_date: string;
  end_date: string | null;
  status: "draft" | "active" | "ended";
  reference: string | null;
  notes: string | null;
};

type StaffProfile = {
  id: string;
  linked_user_id: string | null;
  staff_number: string;
  first_name: string;
  last_name: string;
  email: string | null;
  phone: string | null;
  job_title: string;
  department: string | null;
  hire_date: string | null;
  status: "active" | "inactive";
  notes: string | null;
  qualifications: Qualification[];
  contracts: Contract[];
};

const initialProfile = {
  staff_number: "",
  first_name: "",
  last_name: "",
  job_title: "",
  linked_user_id: "",
  email: "",
  phone: "",
  department: "",
  hire_date: "",
  notes: "",
};

export default function StaffWorkspace() {
  const [profiles, setProfiles] = useState<StaffProfile[]>([]);
  const [teachers, setTeachers] = useState<Teacher[]>([]);
  const [form, setForm] = useState(initialProfile);
  const [editingProfileId, setEditingProfileId] = useState("");
  const [editForm, setEditForm] = useState({
    first_name: "",
    last_name: "",
    job_title: "",
    email: "",
    phone: "",
    department: "",
    hire_date: "",
    notes: "",
  });
  const [qualificationForms, setQualificationForms] = useState<Record<string, boolean>>({});
  const [contractForms, setContractForms] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [savingId, setSavingId] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeTone, setNoticeTone] = useState<"error" | "success" | "info">("info");

  useEffect(() => {
    let active = true;
    async function load() {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      try {
        const account = await schoolApi<{ role: string }>("/api/v1/auth/me");
        if (account.role !== "school_admin") {
          window.location.replace(account.role === "teacher" ? "/" : "/parent");
          return;
        }
        const [staff, schoolTeachers] = await Promise.all([
          schoolApi<StaffProfile[]>("/api/v1/staff"),
          schoolApi<Teacher[]>("/api/v1/teachers"),
        ]);
        if (!active) return;
        setProfiles(staff);
        setTeachers(schoolTeachers);
      } catch (error: unknown) {
        if (active) {
          setNotice(error instanceof Error ? error.message : "Unable to load staff records.");
          setNoticeTone("error");
        }
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => { active = false; };
  }, []);

  async function createProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const { linked_user_id, ...fields } = form;
      const result = await schoolApi<StaffProfile>("/api/v1/staff", {
        method: "POST",
        body: JSON.stringify({
          ...fields,
          linked_user_id: linked_user_id || null,
          email: fields.email || null,
          phone: fields.phone || null,
          department: fields.department || null,
          hire_date: fields.hire_date || null,
          notes: fields.notes || null,
        }),
      });
      setProfiles((current) => [...current, result].sort((a, b) => a.last_name.localeCompare(b.last_name)));
      setForm(initialProfile);
      setNotice("Staff profile created.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to create the staff profile.");
      setNoticeTone("error");
    } finally {
      setSaving(false);
    }
  }

  async function updateStatus(profile: StaffProfile) {
    setSavingId(profile.id);
    try {
      const result = await schoolApi<StaffProfile>(`/api/v1/staff/${encodeURIComponent(profile.id)}`, {
        method: "PATCH",
        body: JSON.stringify({ status: profile.status === "active" ? "inactive" : "active" }),
      });
      setProfiles((current) => current.map((item) => item.id === result.id ? result : item));
      setNotice(profile.status === "active" ? "Staff profile marked inactive." : "Staff profile reactivated.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to update staff status.");
      setNoticeTone("error");
    } finally {
      setSavingId("");
    }
  }

  function beginEditProfile(profile: StaffProfile) {
    setEditingProfileId(profile.id);
    setEditForm({
      first_name: profile.first_name,
      last_name: profile.last_name,
      job_title: profile.job_title,
      email: profile.email ?? "",
      phone: profile.phone ?? "",
      department: profile.department ?? "",
      hire_date: profile.hire_date ?? "",
      notes: profile.notes ?? "",
    });
  }

  async function updateProfile(event: FormEvent<HTMLFormElement>, profileId: string) {
    event.preventDefault();
    setSavingId(profileId);
    try {
      const updated = await schoolApi<StaffProfile>(`/api/v1/staff/${encodeURIComponent(profileId)}`, {
        method: "PATCH",
        body: JSON.stringify({
          ...editForm,
          email: editForm.email || null,
          phone: editForm.phone || null,
          department: editForm.department || null,
          hire_date: editForm.hire_date || null,
          notes: editForm.notes || null,
        }),
      });
      setProfiles((current) => current.map((profile) => profile.id === profileId ? updated : profile));
      setEditingProfileId("");
      setNotice("Staff profile updated.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to update the staff profile.");
      setNoticeTone("error");
    } finally {
      setSavingId("");
    }
  }

  async function addQualification(profileId: string, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    setSavingId(profileId);
    try {
      await schoolApi<Qualification>(`/api/v1/staff/${encodeURIComponent(profileId)}/qualifications`, {
        method: "POST",
        body: JSON.stringify({
          title: formData.get("title"),
          institution: formData.get("institution"),
          field_of_study: formData.get("field_of_study") || null,
          awarded_on: formData.get("awarded_on") || null,
          notes: formData.get("notes") || null,
        }),
      });
      await refreshProfiles();
      setQualificationForms((current) => ({ ...current, [profileId]: false }));
      setNotice("Qualification added.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to add qualification.");
      setNoticeTone("error");
    } finally {
      setSavingId("");
    }
  }

  async function addContract(profileId: string, event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    setSavingId(profileId);
    try {
      await schoolApi<Contract>(`/api/v1/staff/${encodeURIComponent(profileId)}/contracts`, {
        method: "POST",
        body: JSON.stringify({
          contract_type: formData.get("contract_type"),
          start_date: formData.get("start_date"),
          end_date: formData.get("end_date") || null,
          status: formData.get("status"),
          reference: formData.get("reference") || null,
          notes: formData.get("notes") || null,
        }),
      });
      await refreshProfiles();
      setContractForms((current) => ({ ...current, [profileId]: false }));
      setNotice("Contract record added.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to add contract record.");
      setNoticeTone("error");
    } finally {
      setSavingId("");
    }
  }

  async function updateContract(profileId: string, contract: Contract, status: Contract["status"]) {
    setSavingId(contract.id);
    try {
      const updated = await schoolApi<Contract>(
        `/api/v1/staff/${encodeURIComponent(profileId)}/contracts/${encodeURIComponent(contract.id)}`,
        { method: "PATCH", body: JSON.stringify({ status }) },
      );
      setProfiles((current) => current.map((profile) => profile.id === profileId
        ? { ...profile, contracts: profile.contracts.map((item) => item.id === contract.id ? updated : item) }
        : profile));
      setNotice("Contract status updated.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to update contract status.");
      setNoticeTone("error");
    } finally {
      setSavingId("");
    }
  }

  async function refreshProfiles() {
    setProfiles(await schoolApi<StaffProfile[]>("/api/v1/staff"));
  }

  return (
    <div className="student-workspace staff-workspace">
      <aside className="student-sidebar">
        <Link className="brand student-brand" href="/">
          <span className="brand-mark"><GraduationCap size={21} /></span>
          <span><span className="brand-name">northstar</span><span className="brand-caption">STAFF DIRECTORY</span></span>
        </Link>
        <div className="student-school-name">School workspace</div>
        <div className="nav-label">MANAGEMENT</div>
        <Link className="student-side-link" href="/"><ArrowLeft size={17} /> Back to workspace</Link>
        <div className="student-side-link active"><UserRound size={17} /> Staff records</div>
      </aside>
      <main className="student-main">
        <header className="student-topbar"><div><span>Management</span><b>/</b><strong>Staff records</strong></div></header>
        <div className="student-page-content">
          <section className="student-page-heading">
            <div><p className="eyebrow">PEOPLE OPERATIONS</p><h1>Staff records</h1><p className="page-description">Maintain staff profiles, qualifications, and contract details.</p></div>
          </section>

          <section className="panel staff-panel">
            <header className="staff-panel-heading">
              <div><h2>Add a staff profile</h2><p>Link an existing teacher account or add a non-login staff record.</p></div>
            </header>
            <form className="staff-form" onSubmit={(event) => void createProfile(event)}>
              <label>Staff number<input required maxLength={48} value={form.staff_number} onChange={(event) => setForm((current) => ({ ...current, staff_number: event.target.value }))} placeholder="STF-001" /></label>
              <label>First name<input required maxLength={80} value={form.first_name} onChange={(event) => setForm((current) => ({ ...current, first_name: event.target.value }))} /></label>
              <label>Last name<input required maxLength={80} value={form.last_name} onChange={(event) => setForm((current) => ({ ...current, last_name: event.target.value }))} /></label>
              <label>Job title<input required maxLength={100} value={form.job_title} onChange={(event) => setForm((current) => ({ ...current, job_title: event.target.value }))} placeholder="Teacher, Librarian…" /></label>
              <label>Department<input maxLength={100} value={form.department} onChange={(event) => setForm((current) => ({ ...current, department: event.target.value }))} /></label>
              <label>Hire date<input type="date" value={form.hire_date} onChange={(event) => setForm((current) => ({ ...current, hire_date: event.target.value }))} /></label>
              <label>Email<input type="email" maxLength={254} value={form.email} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} /></label>
              <label>Phone<input type="tel" maxLength={32} value={form.phone} onChange={(event) => setForm((current) => ({ ...current, phone: event.target.value }))} /></label>
              <label className="staff-form-wide">Link teacher account
                <select value={form.linked_user_id} onChange={(event) => {
                  const linked = teachers.find((teacher) => teacher.id === event.target.value);
                  setForm((current) => ({ ...current, linked_user_id: event.target.value, email: linked?.email ?? current.email }));
                }}>
                  <option value="">No login account</option>
                  {teachers.map((teacher) => <option key={teacher.id} value={teacher.id}>{teacher.email}{teacher.is_active ? "" : " (invitation pending)"}</option>)}
                </select>
              </label>
              <label className="staff-form-wide">Notes<textarea rows={2} maxLength={5000} value={form.notes} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} /></label>
              <div className="staff-form-actions"><button className="button button-primary" type="submit" disabled={saving}><Plus size={15} />{saving ? "Saving…" : "Create profile"}</button></div>
            </form>
          </section>

          <section className="staff-directory">
            <header className="staff-directory-heading"><div><p className="eyebrow">SCHOOL PERSONNEL</p><h2>Directory</h2></div><span>{profiles.length} staff</span></header>
            {loading ? <p className="staff-empty">Loading staff records…</p> : profiles.length === 0 ? (
              <section className="panel staff-empty">No staff profiles yet. Create a profile to begin recording qualifications and contracts.</section>
            ) : profiles.map((profile) => (
              <article className={`panel staff-profile-card${profile.status === "inactive" ? " staff-profile-inactive" : ""}`} key={profile.id}>
                <header className="staff-profile-heading">
                  <span className="staff-profile-avatar">{profile.first_name[0]}{profile.last_name[0]}</span>
                  <div className="staff-profile-name">
                    <strong>{profile.first_name} {profile.last_name}</strong>
                    <span>{profile.job_title}{profile.department ? ` · ${profile.department}` : ""}</span>
                    <small>{profile.staff_number}{profile.email ? ` · ${profile.email}` : ""}{profile.phone ? ` · ${profile.phone}` : ""}</small>
                  </div>
                  <span className={`staff-state staff-state-${profile.status}`}>{profile.status}</span>
                  <button className="text-button staff-edit-button" type="button" onClick={() => editingProfileId === profile.id ? setEditingProfileId("") : beginEditProfile(profile)}>
                    <Pencil size={13} /> {editingProfileId === profile.id ? "Cancel edit" : "Edit"}
                  </button>
                  <button className="button button-secondary staff-status-button" type="button" disabled={savingId === profile.id} onClick={() => void updateStatus(profile)}>
                    {profile.status === "active" ? "Mark inactive" : "Reactivate"}
                  </button>
                </header>
                {profile.notes && <p className="staff-profile-notes">{profile.notes}</p>}
                {editingProfileId === profile.id && (
                  <form className="staff-inline-form staff-edit-form" onSubmit={(event) => void updateProfile(event, profile.id)}>
                    <label>First name<input required maxLength={80} value={editForm.first_name} onChange={(event) => setEditForm((current) => ({ ...current, first_name: event.target.value }))} /></label>
                    <label>Last name<input required maxLength={80} value={editForm.last_name} onChange={(event) => setEditForm((current) => ({ ...current, last_name: event.target.value }))} /></label>
                    <label>Job title<input required maxLength={100} value={editForm.job_title} onChange={(event) => setEditForm((current) => ({ ...current, job_title: event.target.value }))} /></label>
                    <label>Department<input maxLength={100} value={editForm.department} onChange={(event) => setEditForm((current) => ({ ...current, department: event.target.value }))} /></label>
                    <label>Email<input type="email" maxLength={254} value={editForm.email} onChange={(event) => setEditForm((current) => ({ ...current, email: event.target.value }))} /></label>
                    <label>Phone<input type="tel" maxLength={32} value={editForm.phone} onChange={(event) => setEditForm((current) => ({ ...current, phone: event.target.value }))} /></label>
                    <label>Hire date<input type="date" value={editForm.hire_date} onChange={(event) => setEditForm((current) => ({ ...current, hire_date: event.target.value }))} /></label>
                    <label className="staff-form-wide">Notes<textarea rows={2} maxLength={5000} value={editForm.notes} onChange={(event) => setEditForm((current) => ({ ...current, notes: event.target.value }))} /></label>
                    <button className="button button-primary" type="submit" disabled={savingId === profile.id}><Save size={13} /> Save changes</button>
                  </form>
                )}
                <div className="staff-record-columns">
                  <section className="staff-subrecord">
                    <header><div><GraduationCap size={16} /><strong>Qualifications</strong></div><button className="text-button" type="button" onClick={() => setQualificationForms((current) => ({ ...current, [profile.id]: !current[profile.id] }))}><Plus size={13} /> Add</button></header>
                    {profile.qualifications.length === 0 && !qualificationForms[profile.id] ? <p className="staff-subrecord-empty">No qualifications recorded.</p> : profile.qualifications.map((item) => (
                      <div className="staff-detail-row" key={item.id}><strong>{item.title}</strong><span>{item.institution}{item.field_of_study ? ` · ${item.field_of_study}` : ""}</span>{item.awarded_on && <small>Awarded {item.awarded_on}</small>}{item.notes && <small>{item.notes}</small>}</div>
                    ))}
                    {qualificationForms[profile.id] && (
                      <form className="staff-inline-form" onSubmit={(event) => void addQualification(profile.id, event)}>
                        <label>Qualification<input name="title" required maxLength={160} /></label>
                        <label>Institution<input name="institution" required maxLength={160} /></label>
                        <label>Field of study<input name="field_of_study" maxLength={120} /></label>
                        <label>Award date<input name="awarded_on" type="date" /></label>
                        <label>Notes<textarea name="notes" rows={2} maxLength={500} /></label>
                        <button className="button button-primary" type="submit" disabled={savingId === profile.id}><Save size={13} /> Save qualification</button>
                      </form>
                    )}
                  </section>
                  <section className="staff-subrecord">
                    <header><div><BriefcaseBusiness size={15} /><strong>Contracts</strong></div><button className="text-button" type="button" onClick={() => setContractForms((current) => ({ ...current, [profile.id]: !current[profile.id] }))}><Plus size={13} /> Add</button></header>
                    {profile.contracts.length === 0 && !contractForms[profile.id] ? <p className="staff-subrecord-empty">No contract records.</p> : profile.contracts.map((contract) => (
                      <div className="staff-detail-row staff-contract-row" key={contract.id}>
                        <div><strong>{contract.contract_type}</strong><span className={`contract-state contract-state-${contract.status}`}>{contract.status}</span></div>
                        <span>{contract.start_date}{contract.end_date ? ` – ${contract.end_date}` : " – Ongoing"}</span>
                        {contract.reference && <small>Ref: {contract.reference}</small>}
                        {contract.notes && <small>{contract.notes}</small>}
                        {contract.status !== "ended" && <button className="text-button" type="button" disabled={savingId === contract.id} onClick={() => void updateContract(profile.id, contract, contract.status === "draft" ? "active" : "ended")}>{contract.status === "draft" ? "Activate" : "End contract"}</button>}
                      </div>
                    ))}
                    {contractForms[profile.id] && (
                      <form className="staff-inline-form" onSubmit={(event) => void addContract(profile.id, event)}>
                        <label>Contract type<input name="contract_type" required maxLength={80} placeholder="Full-time, fixed-term…" /></label>
                        <label>Start date<input name="start_date" type="date" required /></label>
                        <label>End date<input name="end_date" type="date" /></label>
                        <label>Reference<input name="reference" maxLength={80} /></label>
                        <label>Status<select name="status"><option value="draft">Draft</option><option value="active">Active</option></select></label>
                        <label>Notes<textarea name="notes" rows={2} maxLength={5000} /></label>
                        <button className="button button-primary" type="submit" disabled={savingId === profile.id}><Save size={13} /> Save contract</button>
                      </form>
                    )}
                  </section>
                </div>
              </article>
            ))}
          </section>
        </div>
      </main>
      <FeedbackModal message={notice} tone={noticeTone} onClose={() => setNotice("")} />
    </div>
  );
}
