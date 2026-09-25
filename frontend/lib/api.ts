export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

function errorMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d: { loc?: (string | number)[]; msg?: string }) => {
          const field = d.loc?.filter((p) => p !== "body").join(".");
          return field ? `${field}: ${d.msg}` : d.msg;
        })
        .join("; ");
    }
  }
  return fallback;
}

/* V8: the hospital this browser tab is showing. Sent with every request so that, if another tab switched hospitals,
   the server answers 409 instead of acting on the other hospital (access itself is decided server-side). */
let activeHospital: string | null = null;
export function setActiveHospital(id: number | null | undefined) {
  activeHospital = id === undefined ? null : String(id ?? "");
}

let refreshing: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  refreshing ??= fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => {
      setTimeout(() => (refreshing = null), 0);
    });
  return refreshing;
}

export async function api<T>(
  path: string,
  opts: { method?: string; body?: unknown; form?: FormData; query?: Query; retry?: boolean } = {},
): Promise<T> {
  const { method = "GET", body, form, query, retry = true } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined && !form) headers["Content-Type"] = "application/json";
  if (activeHospital !== null && !path.startsWith("/auth/") && !/^\/hospitals\/\d+\/switch$/.test(path)) {
    headers["X-MedFlow-Hospital"] = activeHospital;
  }
  const res = await fetch(withQuery(`/api${path}`, query), {
    method,
    credentials: "same-origin",
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });

  if (res.status === 401 && retry && !path.startsWith("/auth/")) {
    if (await refreshSession()) return api<T>(path, { ...opts, retry: false });
    if (typeof window !== "undefined" && window.location.pathname !== "/login") {
      // Hard navigation on purpose: session is gone, drop all client state.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    }
  }
  if (res.status === 204) return undefined as T;

  const data = await res.json().catch(() => null);
  if (res.status === 409 && typeof window !== "undefined" && errorMessage(data, "").startsWith("hospital_changed")) {
    // another tab switched hospitals: reload so this tab shows (and acts on) the hospital that is now active
    window.location.reload();
  }
  if (!res.ok) throw new ApiError(res.status, errorMessage(data, `Request failed (${res.status})`), data);
  return data as T;
}

export const get = <T>(path: string, query?: Query) => api<T>(path, { query });
export const post = <T>(path: string, body?: unknown) => api<T>(path, { method: "POST", body: body ?? {} });
export const patch = <T>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body });
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });
/** V9: multipart upload (the browser sets the boundary header). */
export const upload = <T>(path: string, form: FormData) => api<T>(path, { method: "POST", form });
