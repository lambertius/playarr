import { useEffect, useState } from "react";
import { Check, GripVertical, Plus, Search, X } from "lucide-react";
import type { ConsolidationNameOption } from "@/types";

export interface ConsolidationDraftTarget {
  rawName: string;
  mbid?: string;
  videoCount?: number;
}

export interface ConsolidationColumnsDraft {
  maskName: string;
  targets: ConsolidationDraftTarget[];
  mbids: string[];
}

interface Props {
  kind: "artist" | "genre";
  value: ConsolidationColumnsDraft;
  onChange: (value: ConsolidationColumnsDraft) => void;
  onSave: () => void;
  onCancel: () => void;
  saving?: boolean;
  search: (query: string) => Promise<ConsolidationNameOption[]>;
}

type DraggedName = { name: string; mbid?: string };

function unique(values: string[]): string[] {
  const seen = new Set<string>();
  return values.filter(value => {
    const key = value.trim().toLocaleLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function readDragged(event: React.DragEvent): DraggedName | null {
  try {
    return JSON.parse(event.dataTransfer.getData("application/playarr-consolidation-name")) as DraggedName;
  } catch {
    return null;
  }
}

export function ConsolidationColumnsEditor({ kind, value, onChange, onSave, onCancel, saving, search }: Props) {
  const hasMbids = kind === "artist";
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<ConsolidationNameOption[]>([]);
  const [searching, setSearching] = useState(false);
  const [manualTarget, setManualTarget] = useState("");
  const [manualMbid, setManualMbid] = useState("");

  useEffect(() => {
    if (!query.trim()) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setSearching(true);
      void search(query.trim()).then(found => {
        if (!cancelled) setResults(found);
      }).finally(() => { if (!cancelled) setSearching(false); });
    }, 180);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query, search]);

  const addTarget = (name: string, mbid?: string | null, videoCount?: number) => {
    const rawName = name.trim();
    if (!rawName) return;
    const existing = value.targets.find(target => target.rawName.toLocaleLowerCase() === rawName.toLocaleLowerCase());
    const targets = existing
      ? value.targets.map(target => target === existing ? { ...target, mbid: mbid || target.mbid, videoCount: videoCount ?? target.videoCount } : target)
      : [...value.targets, { rawName, mbid: mbid || undefined, videoCount }];
    onChange({ ...value, targets, mbids: unique([...value.mbids, ...(mbid ? [mbid] : [])]) });
  };

  const setMask = (name: string, mbid?: string | null, videoCount?: number) => {
    const maskName = name.trim();
    if (!maskName) return;
    if (mbid) {
      const existing = value.targets.some(target => target.rawName.toLocaleLowerCase() === maskName.toLocaleLowerCase());
      onChange({
        ...value,
        maskName,
        targets: existing
          ? value.targets.map(target => target.rawName.toLocaleLowerCase() === maskName.toLocaleLowerCase() ? { ...target, mbid, videoCount: videoCount ?? target.videoCount } : target)
          : [...value.targets, { rawName: maskName, mbid, videoCount }],
        mbids: unique([...value.mbids, mbid]),
      });
    } else {
      onChange({ ...value, maskName });
    }
  };

  const removeTarget = (index: number) => {
    const removed = value.targets[index];
    const targets = value.targets.filter((_, targetIndex) => targetIndex !== index);
    const mbidStillUsed = removed.mbid && targets.some(target => target.mbid === removed.mbid);
    const maskWasAttached = value.maskName.toLocaleLowerCase() === removed.rawName.toLocaleLowerCase();
    onChange({
      maskName: maskWasAttached ? "" : value.maskName,
      targets,
      mbids: removed.mbid && !mbidStillUsed ? value.mbids.filter(mbid => mbid !== removed.mbid) : value.mbids,
    });
  };

  const removeMbid = (removedMbid: string) => {
    const removedTargets = value.targets.filter(target => target.mbid === removedMbid);
    const removedNames = new Set(removedTargets.map(target => target.rawName.toLocaleLowerCase()));
    onChange({
      maskName: removedNames.has(value.maskName.toLocaleLowerCase()) ? "" : value.maskName,
      targets: value.targets.filter(target => target.mbid !== removedMbid),
      mbids: value.mbids.filter(mbid => mbid !== removedMbid),
    });
  };

  const startDrag = (event: React.DragEvent, name: string, mbid?: string) => {
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/playarr-consolidation-name", JSON.stringify({ name, mbid }));
  };

  const dropOnMask = (event: React.DragEvent) => {
    event.preventDefault();
    const dragged = readDragged(event);
    if (dragged) setMask(dragged.name, dragged.mbid);
  };

  const dropOnTargets = (event: React.DragEvent) => {
    event.preventDefault();
    const dragged = readDragged(event);
    if (dragged) addTarget(dragged.name, dragged.mbid);
  };

  const columns = hasMbids ? "lg:grid-cols-[1fr_1fr_1.4fr]" : "lg:grid-cols-2";
  return (
    <div className="card p-4 space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-text-primary">Consolidation editor</h3>
        <p className="text-[11px] text-text-muted mt-1">Drag a name into Mask name or Target names, search the library, or type a value manually.</p>
      </div>

      <div className={`grid grid-cols-1 ${columns} gap-3`}>
        {hasMbids && (
          <section className="rounded-lg border border-surface-border bg-surface-dark/40 p-3 min-h-40">
            <h4 className="text-[10px] uppercase tracking-wide font-semibold text-text-muted mb-2">MBID</h4>
            <div className="space-y-2">
              {value.mbids.map(mbid => (
                <div key={mbid} className="flex items-center gap-1 rounded bg-surface-light px-2 py-1.5 font-mono text-[10px] text-text-secondary">
                  <span className="min-w-0 flex-1 break-all">{mbid}</span>
                  <button aria-label={`Remove MBID ${mbid}`} className="text-red-400" onClick={() => removeMbid(mbid)}><X size={12} /></button>
                </div>
              ))}
              {value.mbids.length === 0 && <p className="text-xs text-text-muted">No MBID required. Regex-only consolidations are supported.</p>}
            </div>
            <div className="flex gap-1 mt-3">
              <input className="input-field min-w-0 flex-1 font-mono text-xs" placeholder="Add MBID" value={manualMbid} onChange={event => setManualMbid(event.target.value)} />
              <button className="btn-ghost btn-sm" aria-label="Add MBID" onClick={() => { if (manualMbid.trim()) onChange({ ...value, mbids: unique([...value.mbids, manualMbid]) }); setManualMbid(""); }}><Plus size={13} /></button>
            </div>
          </section>
        )}

        <section className="rounded-lg border border-surface-border bg-surface-dark/40 p-3 min-h-40" onDragOver={event => event.preventDefault()} onDrop={dropOnMask}>
          <h4 className="text-[10px] uppercase tracking-wide font-semibold text-text-muted mb-2">Mask name</h4>
          <input className="input-field w-full" placeholder={`Visible ${kind} name`} value={value.maskName} onChange={event => onChange({ ...value, maskName: event.target.value })} />
          {value.maskName && (
            <div draggable onDragStart={event => startDrag(event, value.maskName)} className="mt-3 flex items-center gap-2 rounded bg-accent/10 border border-accent/30 px-2 py-2 text-sm text-text-primary cursor-grab">
              <GripVertical size={13} className="text-text-muted" /><span className="flex-1">{value.maskName}</span>
              <button aria-label="Clear mask name" className="text-red-400" onClick={() => onChange({ ...value, maskName: "" })}><X size={12} /></button>
            </div>
          )}
          <p className="text-[10px] text-text-muted mt-3">This is the name displayed throughout the library.</p>
        </section>

        <section className="rounded-lg border border-surface-border bg-surface-dark/40 p-3 min-h-40" onDragOver={event => event.preventDefault()} onDrop={dropOnTargets}>
          <h4 className="text-[10px] uppercase tracking-wide font-semibold text-text-muted mb-2">Target {kind === "artist" ? "names" : "genres"}</h4>
          <div className="flex flex-wrap gap-1.5">
            {value.targets.map((target, index) => (
              <div key={`${target.rawName}-${target.mbid ?? ""}`} draggable onDragStart={event => startDrag(event, target.rawName, target.mbid)} className="flex items-center gap-1.5 rounded bg-surface-light px-2 py-1.5 text-xs text-text-secondary cursor-grab">
                <GripVertical size={11} className="text-text-muted" />
                <span>{target.rawName}</span>
                {target.videoCount !== undefined && <span className="text-[9px] text-text-muted">{target.videoCount}</span>}
                {target.mbid && <span title={target.mbid} className="font-mono text-[9px] text-accent">{target.mbid.slice(0, 8)}</span>}
                <button aria-label={`Remove target ${target.rawName}`} className="text-red-400" onClick={() => removeTarget(index)}><X size={11} /></button>
              </div>
            ))}
            {value.targets.length === 0 && <p className="text-xs text-text-muted">Drop or add every raw name that should use this mask.</p>}
          </div>
          <div className="flex gap-1 mt-3">
            <input className="input-field min-w-0 flex-1" placeholder={`Add target ${kind}`} value={manualTarget} onChange={event => setManualTarget(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); addTarget(manualTarget); setManualTarget(""); } }} />
            <button className="btn-ghost btn-sm" aria-label="Add target" onClick={() => { addTarget(manualTarget); setManualTarget(""); }}><Plus size={13} /></button>
          </div>
        </section>
      </div>

      <section className="rounded-lg border border-surface-border p-3">
        <label className="relative block">
          <Search size={13} className="absolute left-2.5 top-2.5 text-text-muted" />
          <input className="input-field w-full pl-8" placeholder={`Search library ${kind} names...`} value={query} onChange={event => setQuery(event.target.value)} />
        </label>
        {query && (
          <div className="mt-2 max-h-44 overflow-y-auto divide-y divide-surface-border rounded border border-surface-border">
            {searching && <p className="p-3 text-xs text-text-muted">Searching...</p>}
            {!searching && results.length === 0 && <p className="p-3 text-xs text-text-muted">No matching library names.</p>}
            {!searching && results.map(option => (
              <div key={`${option.name}-${option.mb_artist_id ?? ""}`} className="flex items-center gap-2 p-2 text-xs">
                <div className="min-w-0 flex-1"><div className="text-text-primary truncate">{option.name}</div><div className="text-[10px] text-text-muted">{option.video_count} video{option.video_count === 1 ? "" : "s"}{option.mb_artist_id ? ` / ${option.mb_artist_id}` : ""}</div></div>
                <button className="btn-ghost btn-xs" onClick={() => setMask(option.name, option.mb_artist_id, option.video_count)}>Use as mask</button>
                <button className="btn-ghost btn-xs" onClick={() => addTarget(option.name, option.mb_artist_id, option.video_count)}>Add target</button>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="flex justify-end gap-2">
        <button className="btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button className="btn-primary btn-sm" disabled={saving || !value.maskName.trim()} onClick={onSave}><Check size={13} /> Save changes</button>
      </div>
    </div>
  );
}
