import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge Tailwind classes safely (deduplication + conflict resolution). */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format bytes into human-readable string. */
export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let size = bytes;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i++;
  }
  return `${size.toFixed(i > 0 ? 1 : 0)} ${units[i]}`;
}

/** Format seconds to mm:ss or hh:mm:ss. */
export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Relative time string, e.g. "3 min ago". */
export function timeAgo(dateStr: string): string {
  // Server stores UTC but may omit timezone suffix — treat as UTC
  let normalized = dateStr;
  if (!normalized.endsWith("Z") && !normalized.includes("+") && !/\d{2}:\d{2}$/.test(normalized.slice(-6))) {
    normalized += "Z";
  }
  const diff = Date.now() - new Date(normalized).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(dateStr).toLocaleDateString();
}

/** Active job status check. */
const ACTIVE_JOB_STATUSES = new Set([
  "queued", "downloading", "downloaded", "remuxing", "analyzing",
  "normalizing", "tagging", "writing_nfo", "asset_fetch",
]);

export function isActiveJob(status: string): boolean {
  return ACTIVE_JOB_STATUSES.has(status);
}
