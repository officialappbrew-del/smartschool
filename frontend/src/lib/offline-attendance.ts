import { schoolApi, SchoolApiError } from "./school-api";

export type AttendanceStatus = "present" | "absent" | "late" | "excused";
export type SyncState = "pending" | "synced" | "failed";

export type ClassSection = {
  id: string;
  name: string;
  academic_year_id: string;
  academic_year: string;
  is_active: boolean;
  student_count: number;
};

export type RosterStudent = {
  student_id: string;
  admission_number: string;
  first_name: string;
  last_name: string;
  attendance_status: AttendanceStatus | null;
  client_timestamp: string | null;
  local_id?: string;
  sync_state?: SyncState;
  sync_error?: string;
};

export type AttendanceRoster = {
  class_section_id: string;
  class_name: string;
  academic_year: string;
  attendance_date: string;
  students: RosterStudent[];
};

export type CachedAccount = {
  id: string;
  email: string;
  role: string;
};

type CachedValue<T> = {
  key: string;
  value: T;
  updated_at: number;
};

export type LocalAttendance = {
  key: string;
  local_id: string;
  student_id: string;
  class_section_id: string;
  attendance_date: string;
  status: AttendanceStatus;
  client_timestamp: string;
  user_id: string;
  sync_state: SyncState;
  sync_error?: string;
};

export type LocalGrade = {
  key: string;
  local_id: string;
  assessment_id: string;
  class_section_id: string;
  student_id: string;
  score: number | null;
  client_timestamp: string;
  user_id: string;
  sync_state: SyncState;
  sync_error?: string;
};

type AttendanceSyncResponse = {
  results: Array<{
    local_id: string;
    status: "synced" | "duplicate";
  }>;
};

export type SyncReport = {
  synced: number;
  failed: number;
  pending: number;
  last_error?: string;
};

const DB_VERSION = 2;
const DB_PREFIX = "northstar-school-offline-";

function openDatabase(tenant: string): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(`${DB_PREFIX}${tenant}`, DB_VERSION);
    request.onupgradeneeded = () => {
      const database = request.result;
      if (!database.objectStoreNames.contains("cache")) {
        database.createObjectStore("cache", { keyPath: "key" });
      }
      if (!database.objectStoreNames.contains("attendance")) {
        const attendance = database.createObjectStore("attendance", {
          keyPath: "key",
        });
        attendance.createIndex("scope", ["class_section_id", "attendance_date"]);
        attendance.createIndex("sync_state", "sync_state");
      }
      if (!database.objectStoreNames.contains("grades")) {
        const grades = database.createObjectStore("grades", { keyPath: "key" });
        grades.createIndex("user_id", "user_id");
        grades.createIndex("sync_state", "sync_state");
        grades.createIndex("assessment", ["assessment_id", "user_id"]);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Unable to open offline storage."));
    request.onblocked = () => reject(new Error("Offline storage is blocked by another browser tab."));
  });
}

function transactionComplete(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error("Offline storage transaction failed."));
    transaction.onabort = () => reject(transaction.error ?? new Error("Offline storage transaction was aborted."));
  });
}

function requestValue<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Offline storage request failed."));
  });
}

async function cacheValue<T>(tenant: string, key: string, value: T): Promise<void> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("cache", "readwrite");
    const complete = transactionComplete(transaction);
    transaction.objectStore("cache").put({ key, value, updated_at: Date.now() });
    await complete;
  } finally {
    database.close();
  }
}

async function readCachedValue<T>(tenant: string, key: string): Promise<T | undefined> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("cache", "readonly");
    const complete = transactionComplete(transaction);
    const cached = await requestValue<CachedValue<T> | undefined>(
      transaction.objectStore("cache").get(key),
    );
    await complete;
    return cached?.value;
  } finally {
    database.close();
  }
}

export function attendanceTenant(): string {
  const hostname = window.location.hostname.toLowerCase();
  const labels = hostname.split(".");
  if (labels.length < 2) {
    throw new Error("Open the school workspace on its tenant subdomain to use attendance.");
  }
  return labels[0];
}

export async function cacheAccount(tenant: string, account: CachedAccount): Promise<void> {
  await cacheValue(tenant, "account", account);
}

export async function getCachedAccount(tenant: string): Promise<CachedAccount | undefined> {
  return readCachedValue(tenant, "account");
}

export async function cacheClasses(tenant: string, classes: ClassSection[]): Promise<void> {
  await cacheValue(tenant, "classes", classes);
}

export async function getCachedClasses(tenant: string): Promise<ClassSection[] | undefined> {
  return readCachedValue(tenant, "classes");
}

export async function cacheOfflineValue<T>(
  tenant: string,
  key: string,
  value: T,
): Promise<void> {
  await cacheValue(tenant, key, value);
}

export async function getOfflineValue<T>(
  tenant: string,
  key: string,
): Promise<T | undefined> {
  return readCachedValue(tenant, key);
}

export async function saveGradeOffline(
  tenant: string,
  userId: string,
  entry: Omit<LocalGrade, "key" | "local_id" | "client_timestamp" | "user_id" | "sync_state" | "sync_error">,
): Promise<LocalGrade> {
  const record: LocalGrade = {
    ...entry,
    key: `${userId}:${entry.assessment_id}:${entry.student_id}`,
    local_id: crypto.randomUUID(),
    client_timestamp: new Date().toISOString(),
    user_id: userId,
    sync_state: "pending",
  };
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("grades", "readwrite");
    const complete = transactionComplete(transaction);
    transaction.objectStore("grades").put(record);
    await complete;
  } finally {
    database.close();
  }
  return record;
}

export async function getLocalGradesForAssessment(
  tenant: string,
  assessmentId: string,
  userId: string,
): Promise<LocalGrade[]> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("grades", "readonly");
    const complete = transactionComplete(transaction);
    const records = await requestValue<LocalGrade[]>(
      transaction.objectStore("grades").index("assessment").getAll([assessmentId, userId]),
    );
    await complete;
    return records;
  } finally {
    database.close();
  }
}

export async function getLocalGradesForUser(
  tenant: string,
  userId: string,
): Promise<LocalGrade[]> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("grades", "readonly");
    const complete = transactionComplete(transaction);
    const records = await requestValue<LocalGrade[]>(
      transaction.objectStore("grades").index("user_id").getAll(userId),
    );
    await complete;
    return records;
  } finally {
    database.close();
  }
}

async function updateLocalGradeIfCurrent(
  tenant: string,
  record: LocalGrade,
  syncState: SyncState,
  syncError?: string,
): Promise<void> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("grades", "readwrite");
    const complete = transactionComplete(transaction);
    const store = transaction.objectStore("grades");
    const request = store.get(record.key) as IDBRequest<LocalGrade | undefined>;
    request.onsuccess = () => {
      const current = request.result;
      if (current?.local_id === record.local_id) {
        store.put({ ...current, sync_state: syncState, sync_error: syncError });
      }
    };
    await complete;
  } finally {
    database.close();
  }
}

async function removeLocalGradeIfCurrent(
  tenant: string,
  record: LocalGrade,
): Promise<void> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("grades", "readwrite");
    const complete = transactionComplete(transaction);
    const store = transaction.objectStore("grades");
    const request = store.get(record.key) as IDBRequest<LocalGrade | undefined>;
    request.onsuccess = () => {
      if (request.result?.local_id === record.local_id) store.delete(record.key);
    };
    await complete;
  } finally {
    database.close();
  }
}

export async function syncPendingGrades(
  tenant: string,
  userId: string,
): Promise<SyncReport> {
  if (!navigator.onLine) return { synced: 0, failed: 0, pending: 0 };
  const records = await getLocalGradesForUser(tenant, userId);
  const pending = records
    .filter((record) => record.sync_state === "pending")
    .sort((left, right) => left.client_timestamp.localeCompare(right.client_timestamp));
  if (pending.length === 0) return { synced: 0, failed: 0, pending: 0 };

  let synced = 0;
  let failed = 0;
  let pendingCount = 0;
  let lastError: string | undefined;
  const byAssessment = new Map<string, LocalGrade[]>();
  for (const record of pending) {
    const group = byAssessment.get(record.assessment_id) ?? [];
    group.push(record);
    byAssessment.set(record.assessment_id, group);
  }

  for (const [assessmentId, assessmentGrades] of byAssessment) {
    for (let offset = 0; offset < assessmentGrades.length; offset += 500) {
      const batch = assessmentGrades.slice(offset, offset + 500);
      try {
        await schoolApi("/api/v1/assessments/" + encodeURIComponent(assessmentId) + "/grades", {
          method: "PUT",
          body: JSON.stringify({
            entries: batch.map((record) => ({
              student_id: record.student_id,
              score: record.score,
            })),
          }),
        });
        for (const record of batch) {
          await removeLocalGradeIfCurrent(tenant, record);
          synced += 1;
        }
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : "Grade sync failed.";
        const isPermanent = error instanceof SchoolApiError
          && error.status >= 400
          && error.status < 500
          && error.status !== 401;
        lastError = message;
        for (const record of batch) {
          await updateLocalGradeIfCurrent(
            tenant,
            record,
            isPermanent ? "failed" : "pending",
            message,
          );
        }
        if (isPermanent) failed += batch.length;
        else pendingCount += batch.length;
      }
    }
  }

  return { synced, failed, pending: pendingCount, last_error: lastError };
}

export async function retryFailedGrades(tenant: string, userId: string): Promise<void> {
  const records = await getLocalGradesForUser(tenant, userId);
  for (const record of records) {
    if (record.sync_state === "failed") {
      await updateLocalGradeIfCurrent(tenant, record, "pending");
    }
  }
}

function rosterCacheKey(classId: string, attendanceDate: string): string {
  return `roster:${classId}:${attendanceDate}`;
}

export async function cacheRoster(
  tenant: string,
  roster: AttendanceRoster,
): Promise<void> {
  await cacheValue(
    tenant,
    rosterCacheKey(roster.class_section_id, roster.attendance_date),
    roster,
  );
}

export async function getCachedRoster(
  tenant: string,
  classId: string,
  attendanceDate: string,
): Promise<AttendanceRoster | undefined> {
  return readCachedValue(tenant, rosterCacheKey(classId, attendanceDate));
}

export async function getLocalAttendance(
  tenant: string,
  classId: string,
  attendanceDate: string,
): Promise<LocalAttendance[]> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("attendance", "readonly");
    const complete = transactionComplete(transaction);
    const records = await requestValue<LocalAttendance[]>(
      transaction
        .objectStore("attendance")
        .index("scope")
        .getAll([classId, attendanceDate]),
    );
    await complete;
    return records;
  } finally {
    database.close();
  }
}

export async function saveAttendanceOffline(
  tenant: string,
  userId: string,
  entry: Omit<LocalAttendance, "key" | "local_id" | "client_timestamp" | "user_id" | "sync_state" | "sync_error">,
): Promise<LocalAttendance> {
  const record: LocalAttendance = {
    ...entry,
    key: `${entry.class_section_id}:${entry.attendance_date}:${entry.student_id}`,
    local_id: crypto.randomUUID(),
    client_timestamp: new Date().toISOString(),
    user_id: userId,
    sync_state: "pending",
  };
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("attendance", "readwrite");
    const complete = transactionComplete(transaction);
    transaction.objectStore("attendance").put(record);
    await complete;
  } finally {
    database.close();
  }
  return record;
}

async function updateLocalRecordIfCurrent(
  tenant: string,
  record: LocalAttendance,
  syncState: SyncState,
  syncError?: string,
): Promise<void> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("attendance", "readwrite");
    const complete = transactionComplete(transaction);
    const store = transaction.objectStore("attendance");
    const request = store.get(record.key) as IDBRequest<LocalAttendance | undefined>;
    request.onsuccess = () => {
      const current = request.result;
      if (current?.local_id === record.local_id) {
        store.put({
          ...current,
          sync_state: syncState,
          sync_error: syncError,
        });
      }
    };
    await complete;
  } finally {
    database.close();
  }
}

export async function syncPendingAttendance(
  tenant: string,
  userId: string,
): Promise<SyncReport> {
  if (!navigator.onLine) {
    return { synced: 0, failed: 0, pending: 0 };
  }
  const database = await openDatabase(tenant);
  let pending: LocalAttendance[];
  try {
    const transaction = database.transaction("attendance", "readonly");
    const complete = transactionComplete(transaction);
    const records = await requestValue<LocalAttendance[]>(
      transaction.objectStore("attendance").index("sync_state").getAll("pending"),
    );
    await complete;
    pending = records
      .filter((record) => record.user_id === userId)
      .sort((left, right) => left.client_timestamp.localeCompare(right.client_timestamp))
      .slice(0, 100);
  } finally {
    database.close();
  }
  if (pending.length === 0) {
    return { synced: 0, failed: 0, pending: 0 };
  }

  try {
    const response = await schoolApi<AttendanceSyncResponse>("/api/v1/attendance/sync", {
      method: "POST",
      body: JSON.stringify({
        entries: pending.map((record) => ({
          local_id: record.local_id,
          student_id: record.student_id,
          class_section_id: record.class_section_id,
          attendance_date: record.attendance_date,
          status: record.status,
          client_timestamp: record.client_timestamp,
        })),
      }),
    });
    const results = new Map(response.results.map((result) => [result.local_id, result]));
    let synced = 0;
    for (const record of pending) {
      if (results.has(record.local_id)) {
        await updateLocalRecordIfCurrent(tenant, record, "synced");
        synced += 1;
      }
    }
    return {
      synced,
      failed: 0,
      pending: pending.length - synced,
    };
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : "Attendance sync failed.";
    const isPermanent = error instanceof SchoolApiError && error.status >= 400 && error.status < 500;
    for (const record of pending) {
      await updateLocalRecordIfCurrent(
        tenant,
        record,
        isPermanent ? "failed" : "pending",
        message,
      );
    }
    return {
      synced: 0,
      failed: isPermanent ? pending.length : 0,
      pending: isPermanent ? 0 : pending.length,
      last_error: message,
    };
  }
}

export async function getLocalAttendanceForUser(
  tenant: string,
  userId: string,
): Promise<LocalAttendance[]> {
  const database = await openDatabase(tenant);
  try {
    const transaction = database.transaction("attendance", "readonly");
    const complete = transactionComplete(transaction);
    const records = await requestValue<LocalAttendance[]>(
      transaction.objectStore("attendance").getAll(),
    );
    await complete;
    return records.filter((record) => record.user_id === userId);
  } finally {
    database.close();
  }
}

export async function retryFailedAttendance(
  tenant: string,
  userId: string,
): Promise<void> {
  const records = await getLocalAttendanceForUser(tenant, userId);
  for (const record of records) {
    if (record.sync_state === "failed") {
      await updateLocalRecordIfCurrent(tenant, record, "pending");
    }
  }
}
