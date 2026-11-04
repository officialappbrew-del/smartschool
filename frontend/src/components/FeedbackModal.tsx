"use client";

import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

type FeedbackTone = "error" | "success" | "info" | "confirm";

type FeedbackModalProps = {
  message: string;
  tone?: FeedbackTone;
  title?: string;
  onClose: () => void;
  onConfirm?: () => void;
  confirmLabel?: string;
  confirming?: boolean;
};

const toneTitles: Record<FeedbackTone, string> = {
  error: "Something went wrong",
  success: "Done",
  info: "Information",
  confirm: "Please confirm",
};

export default function FeedbackModal({
  message,
  tone = "info",
  title,
  onClose,
  onConfirm,
  confirmLabel = "Confirm",
  confirming = false,
}: FeedbackModalProps) {
  const id = useId();
  const dialogRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  const [dismissed, setDismissed] = useState(false);
  onCloseRef.current = onClose;
  const isOpen = Boolean(message) && !dismissed;
  const closeDialog = () => {
    if (confirming) return;
    setDismissed(true);
    onCloseRef.current();
  };
  const closeDialogRef = useRef(closeDialog);
  closeDialogRef.current = closeDialog;

  useEffect(() => {
    setDismissed(false);
  }, [message]);

  useEffect(() => {
    if (!isOpen) return;

    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDialogRef.current();
        return;
      }

      if (event.key !== "Tab" || !dialogRef.current) return;
      const buttons = Array.from(
        dialogRef.current.querySelectorAll<HTMLButtonElement>("button:not(:disabled)"),
      );
      if (buttons.length === 0) return;
      const first = buttons[0];
      const last = buttons[buttons.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const Icon = tone === "error" ? AlertCircle : tone === "success" ? CheckCircle2 : Info;

  return (
    <div
      className="feedback-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !confirming) closeDialog();
      }}
    >
      <section
        ref={dialogRef}
        className={`feedback-modal feedback-modal-${tone}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-message`}
      >
        <div className="feedback-modal-icon"><Icon size={22} /></div>
        <button className="feedback-modal-close" type="button" onClick={closeDialog} aria-label="Close dialog">
          <X size={18} />
        </button>
        <h2 id={`${id}-title`}>{title ?? toneTitles[tone]}</h2>
        <p id={`${id}-message`}>{message}</p>
        <footer>
          {onConfirm ? (
            <>
              <button className="feedback-modal-secondary" type="button" onClick={closeDialog} disabled={confirming}>Cancel</button>
              <button className="feedback-modal-primary" type="button" onClick={() => {
                setDismissed(true);
                onConfirm();
              }} disabled={confirming}>
                {confirming ? "Please wait…" : confirmLabel}
              </button>
            </>
          ) : (
            <button className="feedback-modal-primary" type="button" onClick={closeDialog}>Close</button>
          )}
        </footer>
      </section>
    </div>
  );
}
