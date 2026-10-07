export const num = (v, d = 2) =>
  v === null || v === undefined || Number.isNaN(Number(v))
    ? "—"
    : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });

export const int = (v) => (v === null || v === undefined ? "—" : Math.round(Number(v)).toLocaleString("en-IN"));

export const money = (v, cur = "INR") => {
  const n = Number(v || 0);
  const symbol = cur === "INR" ? "₹" : cur === "USD" ? "$" : "";
  if (n >= 10000000) return `${symbol}${(n / 10000000).toFixed(2)} Cr`;
  if (n >= 100000) return `${symbol}${(n / 100000).toFixed(2)} L`;
  return `${symbol}${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
};

export const inr = (v) => money(v, "INR");

export const dt = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" }) : "—");

export const COMPASS = (deg) => {
  const dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
  return dirs[Math.round((((deg % 360) + 360) % 360) / 22.5) % 16];
};
