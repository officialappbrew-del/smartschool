"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Megaphone, Plus, RotateCw, Send } from "lucide-react";
import type { FormEvent } from "react";
import { schoolApi } from "../lib/school-api";
import FeedbackModal from "./FeedbackModal";

type Announcement = {
  id: string;
  title: string;
  body: string;
  status: "draft" | "published";
  created_at: string;
  published_at: string | null;
  recipient_count: number;
  delivered_count: number;
  failed_count: number;
  pending_count: number;
};

type AnnouncementList = {
  items: Announcement[];
  total: number;
};

export default function AnnouncementWorkspace() {
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [actionId, setActionId] = useState("");
  const [confirmationId, setConfirmationId] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeTone, setNoticeTone] = useState<"error" | "success" | "info">("info");

  useEffect(() => {
    let active = true;
    schoolApi<AnnouncementList>("/api/v1/announcements?limit=100")
      .then((result) => {
        if (active) setAnnouncements(result.items);
      })
      .catch((error: unknown) => {
        if (active) {
          setNotice(error instanceof Error ? error.message : "Unable to load announcements.");
          setNoticeTone("error");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  async function saveDraft(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    try {
      const created = await schoolApi<Announcement>("/api/v1/announcements", {
        method: "POST",
        body: JSON.stringify({ title, body }),
      });
      setTitle("");
      setBody("");
      setAnnouncements((current) => [created, ...current].slice(0, 100));
      setNotice("Announcement saved as a draft.");
      setNoticeTone("success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to save the announcement.");
      setNoticeTone("error");
    } finally {
      setSaving(false);
    }
  }

  async function deliver(id: string, retry: boolean) {
    if (!retry) setConfirmationId("");
    setActionId(id);
    try {
      const result = await schoolApi<Announcement>(
        `/api/v1/announcements/${encodeURIComponent(id)}/${retry ? "deliveries/retry" : "publish"}`,
        { method: "POST" },
      );
      setAnnouncements((current) =>
        current.map((announcement) => announcement.id === id ? result : announcement),
      );
      setNotice(
        result.failed_count > 0
          ? `Published. ${result.delivered_count} of ${result.recipient_count} emails delivered; ${result.failed_count} failed. Retry failed deliveries below.`
          : `Published and emailed to ${result.delivered_count} parent${result.delivered_count === 1 ? "" : "s"}.`,
      );
      setNoticeTone(result.failed_count > 0 ? "error" : "success");
    } catch (error: unknown) {
      setNotice(error instanceof Error ? error.message : "Unable to publish the announcement.");
      setNoticeTone("error");
    } finally {
      setActionId("");
    }
  }

  return (
    <div className="announcement-workspace">
      <section className="panel announcement-card">
        <div className="announcement-section-heading">
          <div>
            <p className="eyebrow">SCHOOL COMMUNICATION</p>
            <h2>Write an announcement</h2>
            <p>Save a draft, then publish it to the parent portal and email guardians.</p>
          </div>
          <Megaphone className="announcement-heading-icon" size={22} />
        </div>
        <form className="announcement-form" onSubmit={(event) => void saveDraft(event)}>
          <label>
            Title
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              maxLength={160}
              required
              placeholder="e.g. School closes early on Friday"
            />
          </label>
          <label>
            Message
            <textarea
              value={body}
              onChange={(event) => setBody(event.target.value)}
              maxLength={10000}
              required
              rows={5}
              placeholder="Write the announcement for parents and guardians."
            />
          </label>
          <div className="announcement-form-footer">
            <span>Parents with an active student record and guardian email will be notified.</span>
            <button className="button button-primary" type="submit" disabled={saving}>
              <Plus size={15} /> {saving ? "Saving…" : "Save draft"}
            </button>
          </div>
        </form>
      </section>

      <section className="panel announcement-card">
        <div className="announcement-section-heading">
          <div>
            <p className="eyebrow">UPDATES</p>
            <h2>Announcements</h2>
            <p>{announcements.length} shown · {announcements.filter((item) => item.status === "draft").length} drafts</p>
          </div>
        </div>
        {loading ? (
          <p className="announcement-empty" aria-live="polite">Loading announcements…</p>
        ) : announcements.length === 0 ? (
          <p className="announcement-empty">No announcements yet. Save a draft to get started.</p>
        ) : (
          <div className="announcement-list">
            {announcements.map((announcement) => (
              <article className="announcement-item" key={announcement.id}>
                <div className="announcement-item-heading">
                  <div>
                    <span className={`announcement-status announcement-status-${announcement.status}`}>
                      {announcement.status === "published" ? <CheckCircle2 size={12} /> : null}
                      {announcement.status}
                    </span>
                    <h3>{announcement.title}</h3>
                  </div>
                  <time dateTime={announcement.published_at ?? announcement.created_at}>
                    {new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(
                      new Date(announcement.published_at ?? announcement.created_at),
                    )}
                  </time>
                </div>
                <p className="announcement-item-body">{announcement.body}</p>
                {announcement.status === "published" && (
                  <p className={`announcement-delivery${announcement.failed_count > 0 ? " announcement-delivery-warning" : ""}`}>
                    {announcement.delivered_count} of {announcement.recipient_count} email deliveries complete
                    {announcement.failed_count > 0 ? ` · ${announcement.failed_count} failed` : ""}
                  </p>
                )}
                <div className="announcement-actions">
                  {announcement.status === "draft" ? (
                    <button
                      className="button button-primary"
                      type="button"
                      disabled={Boolean(actionId)}
                      onClick={() => setConfirmationId(announcement.id)}
                    >
                      <Send size={14} /> Publish
                    </button>
                  ) : announcement.failed_count > 0 || announcement.pending_count > 0 ? (
                    <button
                      className="button button-secondary"
                      type="button"
                      disabled={Boolean(actionId)}
                      onClick={() => void deliver(announcement.id, true)}
                    >
                      <RotateCw size={14} /> {actionId === announcement.id ? "Retrying…" : "Retry email"}
                    </button>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <FeedbackModal
        message={notice}
        tone={noticeTone}
        onClose={() => setNotice("")}
      />
      <FeedbackModal
        message={confirmationId ? "Publish this announcement to parents and email all guardians with an active student record?" : ""}
        tone="confirm"
        title="Publish announcement?"
        confirmLabel="Publish"
        confirming={Boolean(actionId)}
        onClose={() => setConfirmationId("")}
        onConfirm={() => void deliver(confirmationId, false)}
      />
    </div>
  );
}
