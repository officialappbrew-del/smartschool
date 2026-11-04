export function tenantApiUrl(path: string): string {
  const apiBase = new URL(
    process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000",
  );
  apiBase.hostname = window.location.hostname;
  return new URL(path, apiBase).toString();
}
