"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  Flag,
  GraduationCap,
  MessageCircle,
  Send,
  ShieldCheck,
} from "lucide-react";
import type { FormEvent } from "react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type Account = { id: string; email: string; role: string };
type Child = { id: string; first_name: string; last_name: string };
type TeacherContact = { teacher_id: string; email: string; class_name: string };
type Message = {
  id: string;
  sender_user_id: string;
  sender_email: string;
  body: string;
  created_at: string;
  flag_status: "open" | "reviewed" | "dismissed" | null;
  can_flag: boolean;
};
type Thread = {
  id: string;
  student_id: string;
  student_name: string;
  teacher_id: string;
  teacher_email: string;
  guardian_id: string;
  guardian_email: string;
  message_count: number;
  latest_message: string | null;
  updated_at: string;
};
type ThreadDetail = Thread & { messages: Message[] };

function formatMessageDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function MessagingWorkspace() {
  const [account, setAccount] = useState<Account | null>(null);
  const [children, setChildren] = useState<Child[]>([]);
  const [selectedChildId, setSelectedChildId] = useState("");
  const [teachers, setTeachers] = useState<TeacherContact[]>([]);
  const [selectedTeacherId, setSelectedTeacherId] = useState("");
  const [threads, setThreads] = useState<Thread[]>([]);
  const [selectedThreadId, setSelectedThreadId] = useState("");
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [messageBody, setMessageBody] = useState("");
  const [reportId, setReportId] = useState("");
  const [reportReason, setReportReason] = useState("");
  const [loading, setLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [noticeTone, setNoticeTone] = useState<"error" | "success" | "info">("info");

  const refreshThreads = useCallback(async () => {
    const result = await schoolApi<{ items: Thread[]; total: number }>("/api/v1/messages/threads");
    setThreads(result.items);
  }, []);

  useEffect(() => {
    let active = true;
    async function load() {
      if (!window.sessionStorage.getItem("northstar_access_token")) {
        window.location.replace("/login");
        return;
      }
      try {
        const currentAccount = await schoolApi<Account>("/api/v1/auth/me");
        if (currentAccount.role !== "teacher" && currentAccount.role !== "guardian") {
          window.location.replace(currentAccount.role === "school_admin" ? "/" : "/login");
          return;
        }
        const [childList, threadList] = await Promise.all([
          currentAccount.role === "guardian"
            ? schoolApi<Child[]>("/api/v1/parents/children")
            : Promise.resolve([]),
          schoolApi<{ items: Thread[]; total: number }>("/api/v1/messages/threads"),
        ]);
        if (!active) return;
        setAccount(currentAccount);
        setChildren(childList);
        setSelectedChildId(childList[0]?.id ?? "");
        setThreads(threadList.items);
        setSelectedThreadId(threadList.items[0]?.id ?? "");
      } catch (error: unknown) {
        if (active) {
          setNotice(error instanceof Error ? error.message : "Unable to load messages.");
          setNoticeTone("error");
        }
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
    if (account?.role !== "guardian" || !selectedChildId) {
      setTeachers([]);
      setSelectedTeacherId("");
      return;
    }
    let active = true;
    schoolApi<TeacherContact[]>(
      `/api/v1/parents/children/${encodeURIComponent(selectedChildId)}/teachers`,
    )
      .then((result) => {
        if (!active) return;
        const contacts = Array.from(
          result.reduce((grouped, contact) => {
            const existing = grouped.get(contact.teacher_id);
            grouped.set(contact.teacher_id, existing
              ? { ...existing, class_name: `${existing.class_name}, ${contact.class_name}` }
              : contact);
            return grouped;
          }, new Map<string, TeacherContact>()),
        ).map(([, contact]) => contact);
        setTeachers(contacts);
        setSelectedTeacherId(contacts[0]?.teacher_id ?? "");
      })
      .catch((error: unknown) => {
        if (active) {
          setNotice(error instanceof Error ? error.message : "Unable to load your child's teachers.");
          setNoticeTone("error");
        }
      });
    return () => {
      active = false;
    };
  }, [account?.role, selectedChildId]);

  useEffect(() => {
    if (!selectedThreadId) {
      setThread(null);
      return;
    }
    let active = true;
    setContentLoading(true);
    schoolApi<ThreadDetail>(`/api/v1/messages/threads/${encodeURIComponent(selectedThreadId)}`)
      .then((result) => {
        if (active) setThread(result);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setNotice(error instanceof Error ? error.message : "Unable to load this conversation.");
        setNoticeTone("error");
      })
      .finally(() => {
        if (active) setContentLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedThreadId]);

  async function startConversation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const result = await schoolApi<ThreadDetail>("/api/v1/messages/threads", {
        method: "POST",
        body: JSON.stringify({ student_id: selectedChildId, teacher_id: selectedTeacherId }),
      });
      setThreads((current) => current.some((item) => item.id === result.id) ? current : [result, ...current]);
      setSelectedThreadId(result.id);
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to start a conversation.");
      setNoticeTone("error");
    } finally {
      setSaving(false);
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!thread) return;
    setSaving(true);
    try {
      await schoolApi<Message>(`/api/v1/messages/threads/${encodeURIComponent(thread.id)}/messages`, {
        method: "POST",
        body: JSON.stringify({ body: messageBody }),
      });
      setMessageBody("");
      const updatedThread = await schoolApi<ThreadDetail>(
        `/api/v1/messages/threads/${encodeURIComponent(thread.id)}`,
      );
      setThread(updatedThread);
      await refreshThreads();
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to send your message.");
      setNoticeTone("error");
    } finally {
      setSaving(false);
    }
  }

  async function flagMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!reportId) return;
    setSaving(true);
    try {
      await schoolApi(`/api/v1/messages/${encodeURIComponent(reportId)}/flags`, {
        method: "POST",
        body: JSON.stringify({ reason: reportReason }),
      });
      if (thread) {
        setThread(await schoolApi<ThreadDetail>(
          `/api/v1/messages/threads/${encodeURIComponent(thread.id)}`,
        ));
      }
      setReportId("");
      setReportReason("");
      setNotice("The message was flagged for school administrator review.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to flag this message.");
      setNoticeTone("error");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <main className="workspace-loading" aria-live="polite">Loading messages…</main>;
  }

  const canStartConversation = account?.role === "guardian" && teachers.length > 0;
  const accountLabel = account?.role === "teacher" ? "Teacher" : "Parent / guardian";

  return (
    <div className="student-workspace messaging-workspace">
      <aside className="student-sidebar">
        <Link className="brand student-brand" href={account?.role === "teacher" ? "/" : "/parent"}>
          <span className="brand-mark"><GraduationCap size={21} /></span>
          <span><span className="brand-name">northstar</span><span className="brand-caption">MESSAGES</span></span>
        </Link>
        <div className="student-school-name">School workspace</div>
        <div className="nav-label">WORKSPACE</div>
        <Link className="student-side-link" href={account?.role === "teacher" ? "/" : "/parent"}>
          <ArrowLeft size={17} /> Back to workspace
        </Link>
        <div className="student-side-link active"><MessageCircle size={17} /> Messages</div>
        <div className="student-account">
          <span className="student-account-avatar">{account?.email[0]?.toUpperCase() ?? "U"}</span>
          <span><strong>{account?.email}</strong><small>{accountLabel}</small></span>
        </div>
      </aside>

      <main className="student-main">
        <header className="student-topbar">
          <div><span>Workspace</span><b>/</b><strong>Messages</strong></div>
          <span className="message-privacy"><ShieldCheck size={14} /> Private to this school</span>
        </header>
        <div className="student-page-content">
          <section className="student-page-heading">
            <div>
              <p className="eyebrow">FAMILY COMMUNICATION</p>
              <h1>Messages</h1>
              <p className="page-description">Have a private conversation about a student.</p>
            </div>
          </section>

          <section className="panel messaging-panel">
            <aside className="message-thread-list">
              <div className="message-list-heading"><strong>Conversations</strong><span>{threads.length}</span></div>
              {canStartConversation && (
                <form className="message-new-thread" onSubmit={(event) => void startConversation(event)}>
                  <label>
                    Student
                    <select value={selectedChildId} onChange={(event) => setSelectedChildId(event.target.value)}>
                      {children.map((child) => (
                        <option key={child.id} value={child.id}>
                          {child.first_name} {child.last_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Teacher
                    <select value={selectedTeacherId} onChange={(event) => setSelectedTeacherId(event.target.value)}>
                      {teachers.map((teacher) => (
                        <option key={teacher.teacher_id} value={teacher.teacher_id}>
                          {teacher.email} · {teacher.class_name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button className="button button-secondary" type="submit" disabled={saving || !selectedTeacherId}>
                    <MessageCircle size={14} /> Start conversation
                  </button>
                </form>
              )}
              {threads.length === 0 ? (
                <p className="message-empty-list">
                  {account?.role === "teacher"
                    ? "Parents can start a conversation with you from their portal."
                    : teachers.length === 0
                      ? "No teachers are currently assigned to your child's active class."
                      : "Start a conversation with one of your child's teachers."}
                </p>
              ) : (
                <div className="message-thread-items">
                  {threads.map((item) => (
                    <button
                      className={`message-thread-item${selectedThreadId === item.id ? " selected" : ""}`}
                      key={item.id}
                      type="button"
                      onClick={() => setSelectedThreadId(item.id)}
                    >
                      <strong>{account?.role === "teacher" ? item.guardian_email : item.teacher_email}</strong>
                      <span>{item.student_name}</span>
                      <small>{item.latest_message ?? "No messages yet"}</small>
                      <time dateTime={item.updated_at}>{formatMessageDate(item.updated_at)}</time>
                    </button>
                  ))}
                </div>
              )}
            </aside>

            <section className="message-conversation" aria-label="Conversation">
              {!selectedThreadId ? (
                <div className="message-empty-conversation">
                  <MessageCircle size={26} />
                  <strong>Select or start a conversation</strong>
                  <span>Messages about students stay within their school.</span>
                </div>
              ) : contentLoading || !thread ? (
                <p className="message-loading" aria-live="polite">Loading conversation…</p>
              ) : (
                <>
                  <header className="message-conversation-heading">
                    <div>
                      <strong>{thread.student_name}</strong>
                      <span>{account?.role === "teacher" ? thread.guardian_email : thread.teacher_email}</span>
                    </div>
                    <span>{thread.message_count} messages</span>
                  </header>
                  <div className="message-bubble-list">
                    {thread.messages.length === 0 ? (
                      <p className="message-empty-conversation">Send a message to begin this conversation.</p>
                    ) : thread.messages.map((message) => (
                      <article
                        key={message.id}
                        className={`message-bubble${message.sender_user_id === account?.id ? " own" : ""}`}
                      >
                        <header><strong>{message.sender_email}</strong><time dateTime={message.created_at}>{formatMessageDate(message.created_at)}</time></header>
                        <p>{message.body}</p>
                        {message.flag_status && (
                          <span className={`message-flag-status message-flag-${message.flag_status}`}>
                            <Flag size={12} /> {message.flag_status}
                          </span>
                        )}
                        {message.can_flag && (
                          reportId === message.id ? (
                            <form className="message-report-form" onSubmit={(event) => void flagMessage(event)}>
                              <label>
                                Reason for flagging
                                <textarea
                                  value={reportReason}
                                  onChange={(event) => setReportReason(event.target.value)}
                                  minLength={5}
                                  maxLength={500}
                                  required
                                  rows={2}
                                />
                              </label>
                              <div>
                                <button className="message-cancel-report" type="button" onClick={() => setReportId("")}>Cancel</button>
                                <button className="message-flag-button" type="submit" disabled={saving}>Submit flag</button>
                              </div>
                            </form>
                          ) : (
                            <button className="message-flag-button" type="button" onClick={() => setReportId(message.id)}>
                              <Flag size={13} /> Flag for review
                            </button>
                          )
                        )}
                      </article>
                    ))}
                  </div>
                  <form className="message-compose" onSubmit={(event) => void sendMessage(event)}>
                    <textarea
                      value={messageBody}
                      onChange={(event) => setMessageBody(event.target.value)}
                      maxLength={5000}
                      rows={3}
                      required
                      placeholder="Write a message…"
                      aria-label="Write a message"
                    />
                    <button className="button button-primary" type="submit" disabled={saving || !messageBody.trim()}>
                      <Send size={14} /> {saving ? "Sending…" : "Send message"}
                    </button>
                  </form>
                </>
              )}
            </section>
          </section>
        </div>
      </main>
      <FeedbackModal message={notice} tone={noticeTone} onClose={() => setNotice("")} />
    </div>
  );
}

type ModerationFlag = {
  id: string;
  message_id: string;
  thread_id: string;
  student_name: string;
  teacher_email: string;
  guardian_email: string;
  sender_email: string;
  reported_by_email: string;
  body: string;
  reason: string;
  status: "open" | "reviewed" | "dismissed";
  created_at: string;
  reviewed_at: string | null;
};

export function MessageModerationWorkspace() {
  const [flags, setFlags] = useState<ModerationFlag[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeTone, setNoticeTone] = useState<"error" | "success" | "info">("info");

  const loadFlags = useCallback(async () => {
    const result = await schoolApi<{ items: ModerationFlag[]; total: number }>("/api/v1/messages/moderation/flags?flag_status=open");
    setFlags(result.items);
  }, []);

  useEffect(() => {
    let active = true;
    loadFlags()
      .catch((error: unknown) => {
        if (active) {
          setNotice(error instanceof Error ? error.message : "Unable to load flagged messages.");
          setNoticeTone("error");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadFlags]);

  async function resolveFlag(id: string, status: "reviewed" | "dismissed") {
    setActionId(id);
    try {
      await schoolApi(`/api/v1/messages/moderation/flags/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      setFlags((current) => current.filter((flag) => flag.id !== id));
      setNotice(status === "reviewed" ? "Flag marked as reviewed." : "Flag dismissed.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to update the moderation flag.");
      setNoticeTone("error");
    } finally {
      setActionId("");
    }
  }

  return (
    <div className="message-moderation-workspace">
      <section className="panel moderation-panel">
        <header className="moderation-heading">
          <div>
            <p className="eyebrow">SCHOOL ADMINISTRATION</p>
            <h2>Flagged messages</h2>
            <p>Review reports submitted by teachers and guardians.</p>
          </div>
          <ShieldCheck size={22} />
        </header>
        {loading ? (
          <p className="message-loading">Loading moderation queue…</p>
        ) : flags.length === 0 ? (
          <div className="moderation-empty"><ShieldCheck size={23} /><strong>No open flags</strong><span>Reported messages will appear here for review.</span></div>
        ) : (
          <div className="moderation-list">
            {flags.map((flag) => (
              <article className="moderation-item" key={flag.id}>
                <div className="moderation-item-heading">
                  <div><span className="announcement-status">Open report</span><h3>{flag.student_name} · {flag.sender_email}</h3></div>
                  <time dateTime={flag.created_at}>{formatMessageDate(flag.created_at)}</time>
                </div>
                <p className="moderation-message">{flag.body}</p>
                <p className="moderation-reason"><strong>Flagged by {flag.reported_by_email}:</strong> {flag.reason}</p>
                <p className="moderation-participants">Teacher: {flag.teacher_email} · Guardian: {flag.guardian_email}</p>
                <div className="moderation-actions">
                  <button className="button button-secondary" type="button" disabled={Boolean(actionId)} onClick={() => void resolveFlag(flag.id, "dismissed")}>
                    {actionId === flag.id ? "Saving…" : "Dismiss"}
                  </button>
                  <button className="button button-primary" type="button" disabled={Boolean(actionId)} onClick={() => void resolveFlag(flag.id, "reviewed")}>
                    {actionId === flag.id ? "Saving…" : "Mark reviewed"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
      <FeedbackModal message={notice} tone={noticeTone} onClose={() => setNotice("")} />
    </div>
  );
}
