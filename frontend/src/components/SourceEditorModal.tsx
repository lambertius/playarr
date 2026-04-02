import { useState } from "react";
import ReactDOM from "react-dom";
import { X, Plus, Trash2, Pencil, Check, XCircle } from "lucide-react";
import type { SourceInfo, SourceProvider } from "@/types";
import { useCreateSource, useUpdateSource, useDeleteSource } from "@/hooks/queries";
import { useToast } from "@/components/Toast";
import { SourceBadge } from "@/components/Badges";

const PROVIDERS: SourceProvider[] = ["youtube", "vimeo", "wikipedia", "imdb", "musicbrainz", "other"];
const SOURCE_TYPES = ["video", "artist", "album", "single", "recording"] as const;

interface SourceEditorModalProps {
  open: boolean;
  onClose: () => void;
  videoId: number;
  sources: SourceInfo[];
}

interface SourceFormData {
  provider: string;
  source_video_id: string;
  original_url: string;
  canonical_url: string;
  source_type: string;
}

const emptyForm: SourceFormData = {
  provider: "youtube",
  source_video_id: "",
  original_url: "",
  canonical_url: "",
  source_type: "video",
};

function SourceForm({
  data,
  onChange,
  onSubmit,
  onCancel,
  submitLabel,
}: {
  data: SourceFormData;
  onChange: (d: SourceFormData) => void;
  onSubmit: () => void;
  onCancel: () => void;
  submitLabel: string;
}) {
  return (
    <div className="space-y-2 p-3 rounded-lg border border-surface-border bg-surface-dark/50">
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[10px] font-medium text-text-muted uppercase">Provider</label>
          <select
            value={data.provider}
            onChange={(e) => onChange({ ...data, provider: e.target.value })}
            className="w-full rounded-md border border-surface-border bg-surface-dark px-2 py-1.5 text-sm text-text-primary"
          >
            {PROVIDERS.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-[10px] font-medium text-text-muted uppercase">Source Type</label>
          <select
            value={data.source_type}
            onChange={(e) => onChange({ ...data, source_type: e.target.value })}
            className="w-full rounded-md border border-surface-border bg-surface-dark px-2 py-1.5 text-sm text-text-primary"
          >
            {SOURCE_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="text-[10px] font-medium text-text-muted uppercase">Source Video ID</label>
        <input
          value={data.source_video_id}
          onChange={(e) => onChange({ ...data, source_video_id: e.target.value })}
          className="w-full rounded-md border border-surface-border bg-surface-dark px-2 py-1.5 text-sm text-text-primary"
          placeholder="e.g. dQw4w9WgXcQ"
        />
      </div>
      <div>
        <label className="text-[10px] font-medium text-text-muted uppercase">Original URL</label>
        <input
          value={data.original_url}
          onChange={(e) => onChange({ ...data, original_url: e.target.value })}
          className="w-full rounded-md border border-surface-border bg-surface-dark px-2 py-1.5 text-sm text-text-primary"
          placeholder="https://..."
        />
      </div>
      <div>
        <label className="text-[10px] font-medium text-text-muted uppercase">Canonical URL</label>
        <input
          value={data.canonical_url}
          onChange={(e) => onChange({ ...data, canonical_url: e.target.value })}
          className="w-full rounded-md border border-surface-border bg-surface-dark px-2 py-1.5 text-sm text-text-primary"
          placeholder="https://..."
        />
      </div>
      <div className="flex justify-end gap-2 pt-1">
        <button type="button" onClick={onCancel} className="btn-ghost btn-sm">
          <XCircle size={14} /> Cancel
        </button>
        <button type="button" onClick={onSubmit} className="btn-primary btn-sm">
          <Check size={14} /> {submitLabel}
        </button>
      </div>
    </div>
  );
}

export function SourceEditorModal({ open, onClose, videoId, sources }: SourceEditorModalProps) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<SourceFormData>(emptyForm);
  const [adding, setAdding] = useState(false);
  const [addForm, setAddForm] = useState<SourceFormData>(emptyForm);

  const createMutation = useCreateSource(videoId);
  const updateMutation = useUpdateSource(videoId);
  const deleteMutation = useDeleteSource(videoId);
  const { toast } = useToast();

  if (!open) return null;

  const startEdit = (s: SourceInfo) => {
    setEditingId(s.id);
    setEditForm({
      provider: s.provider,
      source_video_id: s.source_video_id,
      original_url: s.original_url,
      canonical_url: s.canonical_url,
      source_type: s.source_type || "video",
    });
    setAdding(false);
  };

  const handleUpdate = () => {
    if (editingId === null) return;
    updateMutation.mutate(
      { sourceId: editingId, data: editForm },
      {
        onSuccess: () => {
          toast({ type: "success", title: "Source updated" });
          setEditingId(null);
        },
        onError: () => toast({ type: "error", title: "Failed to update source" }),
      }
    );
  };

  const handleCreate = () => {
    if (!addForm.original_url.trim()) return;
    createMutation.mutate(addForm, {
      onSuccess: () => {
        toast({ type: "success", title: "Source added" });
        setAdding(false);
        setAddForm(emptyForm);
      },
      onError: () => toast({ type: "error", title: "Failed to add source" }),
    });
  };

  const handleDelete = (sourceId: number) => {
    deleteMutation.mutate(sourceId, {
      onSuccess: () => toast({ type: "success", title: "Source deleted" }),
      onError: () => toast({ type: "error", title: "Failed to delete source" }),
    });
  };

  return ReactDOM.createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      <div
        className="relative z-10 w-full max-w-xl max-h-[90vh] overflow-y-auto rounded-xl border border-surface-border bg-surface-light p-6 shadow-[0_0_40px_rgba(225,29,46,0.1)]"
        style={{ background: "linear-gradient(135deg, rgba(21,25,35,0.97) 0%, rgba(28,34,48,0.92) 100%)" }}
        role="dialog"
        aria-modal="true"
        aria-labelledby="source-editor-title"
      >
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-text-muted hover:text-text-primary"
          aria-label="Close"
        >
          <X size={18} />
        </button>

        <h2 id="source-editor-title" className="text-lg font-semibold text-text-primary mb-4">
          Edit Sources
        </h2>

        {/* Existing sources list */}
        <div className="space-y-2 mb-4">
          {sources.length === 0 && (
            <p className="text-sm text-text-muted italic">No sources yet.</p>
          )}
          {sources.map((s) =>
            editingId === s.id ? (
              <SourceForm
                key={s.id}
                data={editForm}
                onChange={setEditForm}
                onSubmit={handleUpdate}
                onCancel={() => setEditingId(null)}
                submitLabel="Save"
              />
            ) : (
              <div
                key={s.id}
                className="flex items-center gap-3 p-2.5 rounded-lg border border-surface-border bg-surface-dark/30 group"
              >
                <SourceBadge provider={s.provider} iconOnly />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium text-text-secondary capitalize">{s.provider}</span>
                    {s.source_type && (
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-border/50 text-text-muted uppercase">
                        {s.source_type}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-text-muted truncate" title={s.original_url}>
                    {s.original_url}
                  </p>
                </div>
                <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                  <button
                    onClick={() => startEdit(s)}
                    className="p-1 rounded hover:bg-surface-border/50 text-text-muted hover:text-text-primary"
                    title="Edit source"
                  >
                    <Pencil size={14} />
                  </button>
                  <button
                    onClick={() => handleDelete(s.id)}
                    className="p-1 rounded hover:bg-red-500/20 text-text-muted hover:text-red-400"
                    title="Delete source"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            )
          )}
        </div>

        {/* Add new source */}
        {adding ? (
          <SourceForm
            data={addForm}
            onChange={setAddForm}
            onSubmit={handleCreate}
            onCancel={() => { setAdding(false); setAddForm(emptyForm); }}
            submitLabel="Add"
          />
        ) : (
          <button
            onClick={() => { setAdding(true); setEditingId(null); }}
            className="btn-ghost btn-sm w-full justify-center"
          >
            <Plus size={14} /> Add Source
          </button>
        )}
      </div>
    </div>,
    document.body,
  );
}
