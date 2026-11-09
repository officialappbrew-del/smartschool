import { tenantApiUrl } from "./tenant-api";

export type Student = {
  id: string;
  admission_number: string;
  first_name: string;
  last_name: string;
  date_of_birth: string;
  gender: string;
  class_name: string;
  academic_year: string;
  admission_status: "new" | "in_review" | "accepted" | "enrolled" | "waitlist" | "withdrawn";
  guardian_name: string;
  guardian_email: string | null;
  guardian_phone: string;
  medical_notes: string | null;
  is_active: boolean;
};

export type StudentInput = Omit<
  Student,
  "id" | "is_active" | "class_name" | "academic_year"
> & {
  class_section_id: string;
};

export type StudentList = {
  items: Student[];
  total: number;
};

export class SchoolApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "SchoolApiError";
  }
}

export async function schoolApi<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = window.sessionStorage.getItem("northstar_access_token");
  if (!token) {
    throw new Error("Your session has expired. Sign in again to continue.");
  }

  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (!(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(tenantApiUrl(path), {
    ...init,
    headers,
  });

  if (response.status === 401) {
    window.sessionStorage.removeItem("northstar_access_token");
  }
  if (response.status === 204) {
    return undefined as T;
  }

  let result: unknown;
  try {
    result = await response.json();
  } catch {
    throw new Error("The school service returned an invalid response.");
  }
  if (!response.ok) {
    const body = result as { detail?: unknown };
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : body.detail
          ? JSON.stringify(body.detail)
          : "The request could not be completed.";
    throw new SchoolApiError(detail, response.status);
  }
  return result as T;
}
