import {
  Activity, Ban, CheckCircle2, Clapperboard, Download,
  FileSearch, FolderInput, SkipForward, XCircle,
} from "lucide-react";

import type { JobCategory, JobStatusGroup } from "@/types";

export const STATUS_TABS: Array<{
  value: JobStatusGroup;
  label: string;
  icon: typeof Activity;
}> = [
  { value: "active", label: "Active", icon: Activity },
  { value: "complete", label: "Complete", icon: CheckCircle2 },
  { value: "failed", label: "Failed", icon: XCircle },
  { value: "cancelled", label: "Cancelled", icon: Ban },
  { value: "skipped", label: "Skipped", icon: SkipForward },
];

export const CATEGORY_TABS: Array<{
  value: JobCategory | "all";
  label: string;
  icon?: typeof Download;
}> = [
  { value: "all", label: "All" },
  { value: "download", label: "Downloads", icon: Download },
  { value: "import", label: "Imports", icon: FolderInput },
  { value: "video_editor", label: "Video Editor", icon: Clapperboard },
  { value: "scraper", label: "Scraper", icon: FileSearch },
];
