/**
 * ScanOptionsModal — Prompts the user to choose a scan mode before
 * running rename or duplicate scans from the Review Queue.
 */
import ReactDOM from "react-dom";
import { X, FileEdit, Copy, BrainCircuit, ImageOff } from "lucide-react";

type ScanKind = "renames" | "duplicates" | "enrichment" | "artwork";

interface ScanOptionsModalProps {
  kind: ScanKind;
  open: boolean;
  onClose: () => void;
  onScan: (rescanAll: boolean) => void;
  isPending: boolean;
}

const CONFIG: Record<ScanKind, {
  title: string;
  icon: typeof FileEdit;
  options: { label: string; description: string; rescanAll: boolean }[];
}> = {
  renames: {
    title: "Scan Renames",
    icon: FileEdit,
    options: [
      {
        label: "New mismatches only",
        description:
          "Scan for files that don't match the naming convention, skipping any items you've previously dismissed. This is the fastest option and avoids re-flagging items you've already reviewed.",
        rescanAll: false,
      },
      {
        label: "Full library re-scan",
        description:
          "Re-scan every file in the library for naming convention mismatches, including items that were previously dismissed. Use this if you've changed the naming convention or want a fresh audit of all files.",
        rescanAll: true,
      },
    ],
  },
  duplicates: {
    title: "Scan Duplicates",
    icon: Copy,
    options: [
      {
        label: "New duplicates only",
        description:
          "Scan for duplicate videos that haven't been reviewed yet, skipping pairs you've already resolved or dismissed. Best for routine checks after importing new content.",
        rescanAll: false,
      },
      {
        label: "Full library re-scan",
        description:
          "Re-scan the entire library for all duplicate pairs, including those previously resolved. Use this if you've made bulk changes to your library or want to re-evaluate old decisions.",
        rescanAll: true,
      },
    ],
  },
  enrichment: {
    title: "Scan AI Enrichment",
    icon: BrainCircuit,
    options: [
      {
        label: "Unflagged videos only",
        description:
          "Scan for videos that haven't been AI-enriched yet, skipping any already in the review queue. Best for finding newly imported tracks that need AI processing.",
        rescanAll: false,
      },
      {
        label: "Full library re-scan",
        description:
          "Re-scan every video in the library for incomplete AI enrichment, including items already flagged or dismissed. Use this for a complete audit of AI enrichment status.",
        rescanAll: true,
      },
    ],
  },
  artwork: {
    title: "Scan Missing Artwork",
    icon: ImageOff,
    options: [
      {
        label: "Unflagged videos only",
        description:
          "Scan for videos missing poster or thumbnail artwork, skipping any already in the review queue. Best for finding imported tracks that need artwork.",
        rescanAll: false,
      },
      {
        label: "Full library re-scan",
        description:
          "Re-scan every video in the library for missing artwork, including items already flagged or dismissed. Use this for a complete audit of artwork status.",
        rescanAll: true,
      },
    ],
  },
};

export function ScanOptionsModal({ kind, open, onClose, onScan, isPending }: ScanOptionsModalProps) {
  if (!open) return null;

  const { title, icon: Icon, options } = CONFIG[kind];

  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div
        className="relative z-10 w-full max-w-md max-h-[90vh] overflow-y-auto rounded-xl border border-surface-border bg-surface-light p-6 shadow-[0_0_40px_rgba(225,29,46,0.1)]"
        style={{ background: "linear-gradient(135deg, rgba(21,25,35,0.97) 0%, rgba(28,34,48,0.92) 100%)" }}
        role="dialog"
        aria-modal="true"
      >
        <button onClick={onClose} className="absolute top-4 right-4 text-text-muted hover:text-text-primary" aria-label="Close">
          <X size={18} />
        </button>

        <div className="flex items-center gap-2 mb-4">
          <Icon size={18} className="text-accent-red" />
          <h2 className="text-lg font-semibold text-text-primary">{title}</h2>
        </div>

        <div className="space-y-3">
          {options.map((opt) => (
            <button
              key={opt.label}
              onClick={() => onScan(opt.rescanAll)}
              disabled={isPending}
              className="w-full text-left rounded-lg border border-surface-border bg-surface-dark/50 p-4 hover:border-accent-red/40 hover:bg-surface-dark/80 transition-colors disabled:opacity-50"
            >
              <span className="block text-sm font-medium text-text-primary mb-1">{opt.label}</span>
              <span className="block text-xs text-text-muted leading-relaxed">{opt.description}</span>
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
