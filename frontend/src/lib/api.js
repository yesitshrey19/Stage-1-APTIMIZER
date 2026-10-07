import axios from "axios";

const LOCAL_API_BASE = "http://127.0.0.1:8000/api";

function fallbackApiBase() {
  // Production is served behind the API reverse proxy, so an unset backend URL
  // must stay same-origin. Local development runs FastAPI on port 8000 by default.
  return process.env.NODE_ENV === "production" ? "/api" : LOCAL_API_BASE;
}

function normalizeApiBase(configuredUrl) {
  const value = typeof configuredUrl === "string" ? configuredUrl.trim() : "";
  if (!value) return fallbackApiBase();

  // Relative API paths are useful when the frontend and backend share an origin.
  if (value.startsWith("/")) {
    const path = value.replace(/\/+$/, "");
    return /\/api$/i.test(path) ? path : `${path}/api`;
  }

  // Accept common local settings such as `localhost:8000`, while rejecting
  // malformed explicit schemes before Axios reaches the browser URL parser.
  const hasHttpScheme = /^https?:\/\//i.test(value);
  const looksLikeHostAndPort = /^[\w.-]+:\d+(?:\/|$)/.test(value);
  const hasOtherScheme = /^[a-z][a-z\d+.-]*:/i.test(value) && !looksLikeHostAndPort;
  if (hasOtherScheme && !hasHttpScheme) return fallbackApiBase();

  const candidate = hasHttpScheme || hasOtherScheme ? value : `http://${value}`;
  try {
    const url = new URL(candidate);
    if (!["http:", "https:"].includes(url.protocol) || !url.hostname) {
      return fallbackApiBase();
    }

    const path = url.pathname.replace(/\/+$/, "");
    const apiPath = /\/api$/i.test(path) ? path : `${path}/api`;
    return `${url.origin}${apiPath}`;
  } catch {
    return fallbackApiBase();
  }
}

// Exported for the streaming chat, which uses fetch rather than axios: EventSource cannot
// POST, and the conversation has to go up with the request.
export const API_BASE = normalizeApiBase(process.env.REACT_APP_BACKEND_URL);

export const api = axios.create({
  baseURL: API_BASE,
  withCredentials: true,
});

/** The auth headers axios adds by interceptor, for callers that bypass axios. */
export const authHeaders = () => {
  const token = localStorage.getItem("aptimizer_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
};

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("aptimizer_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export function apiError(detail, fallback = "Something went wrong. Please try again.") {
  if (detail == null) return fallback;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail))
    return detail.map((e) => (e && typeof e.msg === "string" ? e.msg : JSON.stringify(e))).join(" ");
  if (detail && typeof detail.msg === "string") return detail.msg;
  return String(detail);
}

/** Turn a thrown request error into a sentence the person reading it can act on.
 *
 *  Axios reports "Network Error" whenever the browser received no response at all, which
 *  covers a refused CORS preflight, a wrong API host, and a backend that is still waking
 *  up. All three look identical to the user and none of them are a wrong password, so say
 *  which of the two it is rather than passing the library's wording through.
 */
export function requestErrorMessage(err, fallback = "Something went wrong. Please try again.") {
  if (err?.response) {
    const detail = err.response.data?.detail;
    if (detail != null) return apiError(detail, fallback);
    // No detail to quote (an unhandled 500 returns plain text, not our JSON shape), so
    // quote the status instead of dropping the only clue the user has.
    return err.response.status
      ? `The server rejected the request (HTTP ${err.response.status}).`
      : fallback;
  }
  const looksUnreachable =
    err?.code === "ERR_NETWORK" || err?.code === "ECONNABORTED" || err?.message === "Network Error";
  if (looksUnreachable)
    return "Cannot reach the server. It may still be starting up — wait a moment and try again.";
  return err?.message || fallback;
}

export async function downloadFile(path, filename) {
  try {
    const res = await api.get(path, { responseType: "blob" });

    // Handle case where server returned a JSON error payload disguised as a blob
    if (res.data instanceof Blob && res.data.type && res.data.type.includes("application/json")) {
      const text = await res.data.text();
      try {
        const json = JSON.parse(text);
        throw new Error(json.detail || "Server error occurred during export.");
      } catch (parseErr) {
        throw new Error(text || "Export failed.");
      }
    }

    // The server can hand back a different format than asked for (a DXF when it cannot
    // write DWG); keep the caller's name but take the extension the server actually sent.
    const disposition = res.headers?.["content-disposition"] || "";
    const served = /filename="?([^";]+)"?/i.exec(disposition)?.[1];
    const servedExt = served && served.includes(".") ? served.split(".").pop() : null;
    let name = filename || served || "download";
    if (servedExt && !name.toLowerCase().endsWith(`.${servedExt.toLowerCase()}`)) {
      name = name.includes(".") ? name.replace(/\.[^.]+$/, `.${servedExt}`) : `${name}.${servedExt}`;
    }

    const blob = res.data instanceof Blob ? res.data : new Blob([res.data]);
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();

    // Do NOT revoke immediately: Chromium requires the blob URL to remain active while downloading
    setTimeout(() => {
      if (link.parentNode) {
        link.parentNode.removeChild(link);
      }
      window.URL.revokeObjectURL(url);
    }, 45000);
    return name;
  } catch (err) {
    if (err.response && err.response.data instanceof Blob) {
      try {
        const text = await err.response.data.text();
        const json = JSON.parse(text);
        err.message = json.detail || err.message;
      } catch (_) {}
    }
    throw err;
  }
}

/** Push the site layout engine's packed blocks into the project's tower list.
 *
 *  The engine decides how many buildings the land takes and how many floors each carries,
 *  so the project's tower list is written from here rather than kept separately. `siteLayout` is the layout the caller just computed — it is sent
 *  because it is usually still ahead of the autosaved copy on the server.
 */
export const syncTowersFromLayout = async (projectId, siteLayout) => {
  if (!projectId) return null;
  const { data } = await api.post(`/projects/${projectId}/towers/sync-from-layout`, {
    site_layout: siteLayout || null,
  });
  return data;
};
