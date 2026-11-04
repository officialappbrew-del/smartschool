"use client";

import Link from "next/link";
import { useState } from "react";
import type { FormEvent } from "react";
import FeedbackModal from "./FeedbackModal";
import { tenantApiUrl } from "../lib/tenant-api";

type AuthMode = "accept-invitation" | "login" | "request-reset" | "reset-password";

type SchoolAuthFormProps = {
  mode: AuthMode;
  token?: string;
};

const formCopy: Record<AuthMode, { title: string; description: string }> = {
  "accept-invitation": {
    title: "Accept your invitation",
    description: "Choose a password to activate your Northstar account.",
  },
  login: {
    title: "Welcome back",
    description: "Sign in to your school account.",
  },
  "request-reset": {
    title: "Reset your password",
    description: "Enter your school account email and we’ll send reset instructions.",
  },
  "reset-password": {
    title: "Choose a new password",
    description: "Use a new password with at least 12 characters.",
  },
};

export default function SchoolAuthForm({ mode, token = "" }: SchoolAuthFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [completed, setCompleted] = useState(false);
  const needsToken = mode === "accept-invitation" || mode === "reset-password";
  const missingTokenMessage =
    needsToken && !token
      ? "This link is missing its security token. Request a new link and try again."
      : "";
  const feedbackMessage = error || message || missingTokenMessage;
  const feedbackTone = error || missingTokenMessage ? "error" : "success";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (completed) return;
    setMessage("");
    setError("");

    if (needsToken && !token) {
      setError("This link is missing its security token. Request a new link and try again.");
      return;
    }
    if (needsToken && password !== confirmPassword) {
      setError("The passwords do not match.");
      return;
    }

    const endpoint = {
      "accept-invitation": "/api/v1/auth/invitations/accept",
      login: "/api/v1/auth/login",
      "request-reset": "/api/v1/auth/password-reset/request",
      "reset-password": "/api/v1/auth/password-reset/confirm",
    }[mode];

    const payload =
      mode === "login"
        ? { email, password }
        : mode === "request-reset"
          ? { email }
          : { token, password };

    setSubmitting(true);
    try {
      const response = await fetch(tenantApiUrl(endpoint), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      let result: Record<string, unknown>;
      try {
        result = await response.json();
      } catch {
        throw new Error("The server returned an invalid response. Please try again.");
      }

      if (!response.ok) {
        throw new Error(
          typeof result.detail === "string"
            ? result.detail
            : "The request could not be completed. Please try again.",
        );
      }

      if (mode === "login") {
        if (typeof result.access_token !== "string") {
          throw new Error("The server did not return a sign-in token.");
        }
        window.sessionStorage.setItem("northstar_access_token", result.access_token);
        window.location.assign(
          result.role === "guardian"
            ? "/parent"
            : result.role === "teacher"
              ? "/"
              : "/students",
        );
        return;
      } else {
        setMessage(
          typeof result.message === "string"
            ? result.message
            : "Your request was completed.",
        );
      }
      setCompleted(true);
    } catch (submitError: unknown) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "Unable to contact the school service. Please try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <Link className="auth-brand" href="/">
          <span className="auth-brand-mark">N</span>
          <span>northstar</span>
        </Link>
        <p className="auth-eyebrow">NORTHSTAR SCHOOL PORTAL</p>
        <h1 id="auth-title">{formCopy[mode].title}</h1>
        <p className="auth-description">{formCopy[mode].description}</p>

        <form className="auth-form" onSubmit={submit}>
          {(mode === "login" || mode === "request-reset") && (
            <label className="auth-field">
              Email address
              <input
                type="email"
                name="email"
                autoComplete="email"
                required
                maxLength={254}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </label>
          )}

          {(mode === "login" || needsToken) && (
            <label className="auth-field">
              {mode === "login" ? "Password" : "New password"}
              <input
                type="password"
                name="password"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
                required
                minLength={mode === "login" ? 1 : 12}
                maxLength={128}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
          )}

          {needsToken && (
            <label className="auth-field">
              Confirm new password
              <input
                type="password"
                name="confirm-password"
                autoComplete="new-password"
                required
                minLength={12}
                maxLength={128}
                value={confirmPassword}
                onChange={(event) => setConfirmPassword(event.target.value)}
              />
            </label>
          )}

          {!missingTokenMessage && (
            <button className="auth-submit" type="submit" disabled={submitting || completed}>
              {submitting ? "Please wait..." : mode === "login" ? "Sign in" : "Continue"}
            </button>
          )}
        </form>

        <nav className="auth-links" aria-label="Account options">
          {mode === "login" && (
            <Link href={completed ? "/" : "/password/forgot"}>
              {completed ? "Open the school workspace" : "Forgot your password?"}
            </Link>
          )}
          {mode === "request-reset" && <Link href="/login">Back to sign in</Link>}
          {(mode === "accept-invitation" || mode === "reset-password") && (
            <Link href="/login">Back to sign in</Link>
          )}
        </nav>
      </section>
    </main>
    <FeedbackModal
      message={feedbackMessage}
      tone={feedbackTone}
      onClose={() => {
        setError("");
        setMessage("");
      }}
    />
    </>
  );
}
