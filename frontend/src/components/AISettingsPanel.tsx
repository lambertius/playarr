import { useState, useEffect } from "react";
import { Bot, Key, Save, Server, Sparkles, Zap, Brain, Fingerprint, TestTube2, Check, X, ChevronDown, ChevronRight, Info, FileText, RotateCcw, Activity, Image } from "lucide-react";
import { Tooltip } from "@/components/Tooltip";
import { useAISettings, useAIUpdateSettings, useAITestConnection, useModelCatalog, useRoutingPreview, useAIPrompts, useAIUpdatePrompts, useTestModelAvailability } from "@/hooks/queries";
import { ErrorState, Skeleton } from "@/components/Feedback";
import { useToast } from "@/components/Toast";
import type { AISettingsUpdate, AIPromptSettingsUpdate, ModelAvailabilityEntry } from "@/types";
import { ALL_ENRICHABLE_FIELDS } from "@/types";

const PROVIDERS = [
  { value: "none", label: "Disabled" },
  { value: "openai", label: "OpenAI", hint: "API key required" },
  { value: "gemini", label: "Google Gemini", hint: "API key required" },
  { value: "claude", label: "Anthropic Claude", hint: "API key required" },
  { value: "local", label: "Local LLM (Ollama)", hint: "Local server required" },
];

const TASK_LABELS: Record<string, string> = {
  enrichment: "Metadata Enrichment",
  verification: "Verification",
  scene_ranking: "Scene Ranking",
  fallback: "Fallback / Retry",
};

export function AISettingsPanel() {
  const { toast } = useToast();
  const { data: settings, isLoading, isError, refetch } = useAISettings();
  const updateMutation = useAIUpdateSettings();
  const testMutation = useAITestConnection();
  const availabilityMutation = useTestModelAvailability();

  const [draft, setDraft] = useState<AISettingsUpdate>({});
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
  const [acoustidKey, setAcoustidKey] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const activeProvider = draft.provider ?? settings?.provider ?? "none";
  const providerEnabled = activeProvider !== "none";
  const selectionMode = draft.model_selection_mode ?? settings?.model_selection_mode ?? "auto";

  // Fetch model catalog for active provider
  const { data: catalog, isLoading: catalogLoading } = useModelCatalog(activeProvider);
  // Fetch routing preview
  const { data: routingPreview } = useRoutingPreview();

  // When provider changes, validate stored model selections
  useEffect(() => {
    if (!catalog || !settings || !providerEnabled) return;
    const validIds = new Set(catalog.models.map(m => m.id));
    const defaultModel = catalog.defaults?.manual_default || catalog.models[0]?.id;
    const updates: AISettingsUpdate = {};
    let needsUpdate = false;

    if (settings.model_default && !validIds.has(settings.model_default)) {
      updates.model_default = defaultModel;
      needsUpdate = true;
      toast({ type: "info", title: `Model "${settings.model_default}" not available; reverted to default` });
    }
    if (settings.model_fallback && !validIds.has(settings.model_fallback)) {
      updates.model_fallback = defaultModel;
      needsUpdate = true;
    }
    if (settings.model_metadata && !validIds.has(settings.model_metadata)) {
      updates.model_metadata = defaultModel;
      needsUpdate = true;
    }
    if (settings.model_verification && !validIds.has(settings.model_verification)) {
      updates.model_verification = defaultModel;
      needsUpdate = true;
    }
    if (settings.model_scene && !validIds.has(settings.model_scene)) {
      updates.model_scene = defaultModel;
      needsUpdate = true;
    }
    if (needsUpdate) {
      updateMutation.mutate(updates);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog?.provider]);

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-14 rounded-lg" />
        <Skeleton className="h-14 rounded-lg" />
        <Skeleton className="h-14 rounded-lg" />
      </div>
    );
  }

  if (isError || !settings) {
    return <ErrorState message="Failed to load AI settings" onRetry={refetch} />;
  }

  const save = (updates: AISettingsUpdate) => {
    updateMutation.mutate(updates, {
      onSuccess: () => {
        toast({ type: "success", title: "AI settings saved" });
        setDraft({});
        setApiKeys({});
      },
      onError: () => toast({ type: "error", title: "Failed to save AI settings" }),
    });
  };

  const testConnection = () => {
    testMutation.mutate(
      { provider: activeProvider },
      {
        onSuccess: (res) => {
          if (res.success) {
            toast({ type: "success", title: res.message });
          } else {
            toast({ type: "error", title: res.message });
          }
        },
        onError: () => toast({ type: "error", title: "Connection test failed" }),
      },
    );
  };

  const modelOptions = catalog?.models ?? [];
  const catalogDefault = catalog?.defaults?.manual_default || modelOptions[0]?.id || "";

  // Active enrichable fields
  const activeFields = draft.enrichable_fields ?? settings.enrichable_fields ?? [...ALL_ENRICHABLE_FIELDS];
  const toggleField = (field: string) => {
    const next = activeFields.includes(field)
      ? activeFields.filter((f) => f !== field)
      : [...activeFields, field];
    save({ enrichable_fields: next });
  };

  return (
    <div className="space-y-6">
      {/* Provider Selection */}
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <Bot size={16} className="text-accent" />
            <label className="text-sm font-medium text-text-primary">AI Provider</label>
          </div>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">
            Select which AI service to use for metadata enrichment and plot generation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={activeProvider}
            onChange={(e) => save({ provider: e.target.value })}
            className="input-field w-52 text-sm"
            disabled={updateMutation.isPending}
          >
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}{p.hint ? ` (${p.hint})` : ""}
              </option>
            ))}
          </select>
          {providerEnabled && (
            <Tooltip content="Test connection to the selected AI provider">
            <button
              onClick={testConnection}
              className="btn-secondary btn-sm flex items-center gap-1"
              disabled={testMutation.isPending}
            >
              <TestTube2 size={14} />
              {testMutation.isPending ? "…" : "Test"}
            </button>
            </Tooltip>
          )}
        </div>
      </div>

      {/* Test result */}
      {testMutation.data && (
        <div className={`flex items-center gap-2 text-xs px-3 py-2 rounded-lg ${
          testMutation.data.success
            ? "bg-green-500/10 text-green-400"
            : "bg-red-500/10 text-red-400"
        }`}>
          {testMutation.data.success ? <Check size={14} /> : <X size={14} />}
          <span>{testMutation.data.message}</span>
          {testMutation.data.model_name && testMutation.data.success && (
            <span className="ml-auto text-text-muted">
              Model: {testMutation.data.model_name}
              {testMutation.data.response_time_ms != null && ` · ${testMutation.data.response_time_ms}ms`}
            </span>
          )}
        </div>
      )}

      {/* API Keys */}
      {activeProvider === "openai" && (
        <APIKeyRow
          label="OpenAI API Key"
          isSet={settings.openai_api_key_set}
          value={apiKeys.openai || ""}
          onChange={(v) => setApiKeys({ ...apiKeys, openai: v })}
          onSave={() => save({ openai_api_key: apiKeys.openai })}
          isPending={updateMutation.isPending}
        />
      )}

      {activeProvider === "gemini" && (
        <APIKeyRow
          label="Gemini API Key"
          isSet={settings.gemini_api_key_set}
          value={apiKeys.gemini || ""}
          onChange={(v) => setApiKeys({ ...apiKeys, gemini: v })}
          onSave={() => save({ gemini_api_key: apiKeys.gemini })}
          isPending={updateMutation.isPending}
        />
      )}

      {activeProvider === "claude" && (
        <APIKeyRow
          label="Claude API Key"
          isSet={settings.claude_api_key_set}
          value={apiKeys.claude || ""}
          onChange={(v) => setApiKeys({ ...apiKeys, claude: v })}
          onSave={() => save({ claude_api_key: apiKeys.claude })}
          isPending={updateMutation.isPending}
        />
      )}

      {activeProvider === "local" && (
        <>
          <div className="flex flex-col sm:flex-row sm:items-start gap-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <Server size={16} className="text-text-muted" />
                <label className="text-sm font-medium text-text-primary">Local LLM URL</label>
              </div>
              <p className="text-xs text-text-muted mt-0.5">OpenAI-compatible endpoint (Ollama, LM Studio, etc.)</p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={draft.local_llm_base_url ?? settings.local_llm_base_url}
                onChange={(e) => setDraft({ ...draft, local_llm_base_url: e.target.value })}
                className="input-field w-64 text-sm"
                placeholder="http://localhost:11434/v1"
              />
              {draft.local_llm_base_url && (
                <button onClick={() => save({ local_llm_base_url: draft.local_llm_base_url })} className="btn-primary btn-sm" disabled={updateMutation.isPending}>
                  <Save size={14} />
                </button>
              )}
            </div>
          </div>
        </>
      )}

      {/* ── Model Routing ── */}
      {providerEnabled && (
        <div className="border-t border-white/5 pt-4 space-y-4">
          <div className="flex items-center gap-2 mb-2">
            <Brain size={16} className="text-purple-400" />
            <h4 className="text-sm font-medium text-text-primary">Model Routing</h4>
            {catalogLoading && <span className="text-xs text-text-muted animate-pulse">Loading models…</span>}
            <Tooltip content="Test all configured models for availability">
            <button
              onClick={() => availabilityMutation.mutate(false)}
              className="btn-secondary btn-sm flex items-center gap-1 ml-auto"
              disabled={availabilityMutation.isPending}
            >
              <Activity size={14} />
              {availabilityMutation.isPending ? "Testing…" : "Test Models"}
            </button>
            </Tooltip>
          </div>

          {/* Model Availability Results */}
          {availabilityMutation.data && (
            <div className="bg-surface-dark/50 rounded-lg border border-white/5 p-3 space-y-2">
              <div className="flex items-center justify-between text-xs text-text-muted">
                <span>
                  {availabilityMutation.data.provider} models
                  {availabilityMutation.data.cached && " (cached)"}
                </span>
                <span>{new Date(availabilityMutation.data.tested_at).toLocaleTimeString()}</span>
              </div>
              {availabilityMutation.data.results.map((r: ModelAvailabilityEntry) => (
                <div
                  key={r.model_id}
                  className={`flex items-center gap-2 text-xs px-2 py-1.5 rounded ${
                    r.available
                      ? "bg-green-500/10 text-green-400"
                      : "bg-red-500/10 text-red-400"
                  }`}
                >
                  {r.available ? <Check size={13} /> : <X size={13} />}
                  <span className="font-mono text-[11px]">{r.model_id}</span>
                  {r.available && r.response_time_ms > 0 && (
                    <span className="ml-auto text-text-muted">{r.response_time_ms}ms</span>
                  )}
                  {!r.available && r.error && (
                    <span className="ml-auto text-red-400/70 truncate max-w-[200px]" title={r.error}>
                      {r.error}
                    </span>
                  )}
                </div>
              ))}
              {availabilityMutation.data.cached && (
                <button
                  onClick={() => availabilityMutation.mutate(true)}
                  className="text-xs text-accent hover:underline flex items-center gap-1 mt-1"
                  disabled={availabilityMutation.isPending}
                >
                  <RotateCcw size={11} /> Re-test (bypass cache)
                </button>
              )}
            </div>
          )}

          <div className="flex flex-col sm:flex-row sm:items-start gap-2">
            <div className="flex-1 min-w-0">
              <label className="text-sm font-medium text-text-primary">Selection Mode</label>
              <p className="text-xs text-text-muted mt-0.5">
                Auto: routes tasks to optimal models by tier. Manual: use a single model for everything.
              </p>
            </div>
            <select
              value={selectionMode}
              onChange={(e) => save({ model_selection_mode: e.target.value })}
              className="input-field w-40 text-sm"
              disabled={updateMutation.isPending}
            >
              <option value="auto">Auto</option>
              <option value="manual">Manual</option>
            </select>
          </div>

          {/* ── Manual Mode ── */}
          {selectionMode === "manual" && modelOptions.length > 0 && (
            <>
              <ModelSelect
                label="Default Model"
                description="Used for all tasks unless overridden below."
                value={draft.model_default ?? settings.model_default ?? catalogDefault}
                options={modelOptions.map(m => ({ value: m.id, label: m.label }))}
                onChange={(v) => save({ model_default: v })}
                isPending={updateMutation.isPending}
              />
              <ModelSelect
                label="Fallback Model"
                description="Used when the default model fails or returns low confidence."
                value={draft.model_fallback ?? settings.model_fallback ?? catalogDefault}
                options={modelOptions.map(m => ({ value: m.id, label: m.label }))}
                onChange={(v) => save({ model_fallback: v })}
                isPending={updateMutation.isPending}
              />

              {/* Advanced per-task overrides */}
              <button
                onClick={() => setShowAdvanced(!showAdvanced)}
                className="flex items-center gap-1 text-xs text-text-muted hover:text-text-primary transition-colors ml-4"
              >
                {showAdvanced ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                Advanced: per-task model overrides
              </button>
              {showAdvanced && (
                <div className="ml-4 space-y-3 border-l-2 border-white/5 pl-4">
                  <ModelSelect
                    label="Metadata Model"
                    description="Override for enrichment & correction tasks"
                    value={draft.model_metadata ?? settings.model_metadata ?? ""}
                    options={[{ value: "", label: "(use default)" }, ...modelOptions.map(m => ({ value: m.id, label: m.label }))]}
                    onChange={(v) => save({ model_metadata: v || undefined })}
                    isPending={updateMutation.isPending}
                  />
                  <ModelSelect
                    label="Verification Model"
                    description="Override for truth verification tasks"
                    value={draft.model_verification ?? settings.model_verification ?? ""}
                    options={[{ value: "", label: "(use default)" }, ...modelOptions.map(m => ({ value: m.id, label: m.label }))]}
                    onChange={(v) => save({ model_verification: v || undefined })}
                    isPending={updateMutation.isPending}
                  />
                  <ModelSelect
                    label="Scene Analysis Model"
                    description="Override for AI-assisted scene ranking"
                    value={draft.model_scene ?? settings.model_scene ?? ""}
                    options={[{ value: "", label: "(use default)" }, ...modelOptions.map(m => ({ value: m.id, label: m.label }))]}
                    onChange={(v) => save({ model_scene: v || undefined })}
                    isPending={updateMutation.isPending}
                  />
                </div>
              )}
            </>
          )}

          {/* ── Auto Mode ── */}
          {selectionMode === "auto" && (
            <>
              <div className="flex flex-col sm:flex-row sm:items-start gap-2 ml-4">
                <div className="flex-1 min-w-0">
                  <label className="text-sm font-medium text-text-primary">Tier Preference</label>
                  <p className="text-xs text-text-muted mt-0.5">
                    Shifts model tiers: cheapest uses faster models, accuracy uses stronger ones.
                  </p>
                </div>
                <select
                  value={draft.auto_tier_preference ?? settings.auto_tier_preference ?? "balanced"}
                  onChange={(e) => save({ auto_tier_preference: e.target.value })}
                  className="input-field w-44 text-sm"
                  disabled={updateMutation.isPending}
                >
                  <option value="cheapest">Prefer Cheapest</option>
                  <option value="balanced">Balanced</option>
                  <option value="accuracy">Prefer Accuracy</option>
                </select>
              </div>

              {/* Routing Preview Table */}
              {routingPreview && routingPreview.entries.length > 0 && (
                <div className="ml-4">
                  <div className="flex items-center gap-1.5 mb-2">
                    <Info size={13} className="text-text-muted" />
                    <span className="text-xs font-medium text-text-muted">Routing Preview</span>
                  </div>
                  <div className="bg-surface-dark/50 rounded-lg border border-white/5 overflow-hidden">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="border-b border-white/5">
                          <th className="text-left px-3 py-2 text-text-muted font-medium">Task</th>
                          <th className="text-left px-3 py-2 text-text-muted font-medium">Selected Model</th>
                          <th className="text-left px-3 py-2 text-text-muted font-medium hidden sm:table-cell">Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {routingPreview.entries.map((entry) => (
                          <tr key={entry.task} className="border-b border-white/5 last:border-0">
                            <td className="px-3 py-2 text-text-primary">{TASK_LABELS[entry.task] ?? entry.task}</td>
                            <td className="px-3 py-2 text-accent font-mono text-[11px]">{entry.model_label || entry.model_id}</td>
                            <td className="px-3 py-2 text-text-muted hidden sm:table-cell">{entry.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* Tier defaults (read-only info) */}
              {catalog?.defaults?.auto_tiers && (
                <div className="ml-4 text-xs text-text-muted">
                  <span className="font-medium">Tier defaults:</span>{" "}
                  {Object.entries(catalog.defaults.auto_tiers).map(([tier, modelId]) => (
                    <span key={tier} className="mr-3">
                      <span className="capitalize">{tier}</span> → <span className="text-text-primary font-mono text-[11px]">{modelId as string}</span>
                    </span>
                  ))}
                </div>
              )}
            </>
          )}

          {/* Local provider: show discovered models as select */}
          {activeProvider === "local" && modelOptions.length > 0 && (
            <ModelSelect
              label="Model"
              description="Select from locally installed models"
              value={draft.local_llm_model ?? settings.local_llm_model ?? modelOptions[0]?.id}
              options={modelOptions.map(m => ({ value: m.id, label: m.label }))}
              onChange={(v) => save({ local_llm_model: v })}
              isPending={updateMutation.isPending}
            />
          )}
        </div>
      )}

      {/* ── Enrichable Fields ── */}
      {providerEnabled && (
        <div className="border-t border-white/5 pt-4">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles size={16} className="text-yellow-400" />
            <h4 className="text-sm font-medium text-text-primary">Enrichable Fields</h4>
          </div>
          <p className="text-xs text-text-muted mb-3">
            Select which metadata fields AI is allowed to modify globally.
            Fields can also be overridden per-enrich run.
          </p>
          <div className="flex flex-wrap gap-2">
            {ALL_ENRICHABLE_FIELDS.map((field) => {
              const active = activeFields.includes(field);
              return (
                <button
                  key={field}
                  onClick={() => toggleField(field)}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                    active
                      ? "bg-accent/20 text-accent border border-accent/30"
                      : "bg-surface-light text-text-muted border border-white/5"
                  }`}
                  disabled={updateMutation.isPending}
                >
                  {field}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Auto-Apply Threshold ── */}
      <div className={`border-t border-white/5 pt-4 ${!providerEnabled ? "opacity-50" : ""}`}>
        <div className="flex flex-col sm:flex-row sm:items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <Zap size={16} className="text-green-400" />
              <label className="text-sm font-medium text-text-primary">Auto-Apply Threshold</label>
            </div>
            <p className="text-xs text-text-muted mt-0.5">
              Minimum AI confidence (0–1) to automatically apply field corrections.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="number"
              step="0.05"
              min="0"
              max="1"
              value={draft.auto_apply_threshold ?? settings.auto_apply_threshold}
              onChange={(e) => setDraft({ ...draft, auto_apply_threshold: parseFloat(e.target.value) })}
              className="input-field w-24 text-sm"
              disabled={!providerEnabled || updateMutation.isPending}
            />
            {draft.auto_apply_threshold != null && (
              <button
                onClick={() => save({ auto_apply_threshold: draft.auto_apply_threshold })}
                className="btn-primary btn-sm"
                disabled={!providerEnabled || updateMutation.isPending}
              >
                <Save size={14} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── Thumbnail Ranking Mode ── */}
      <div className={`border-t border-white/5 pt-4 ${!providerEnabled ? "opacity-50" : ""}`}>
        <div className="flex flex-col sm:flex-row sm:items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <Image size={16} className="text-purple-400" />
              <label className="text-sm font-medium text-text-primary">Thumbnail Ranking</label>
            </div>
            <p className="text-xs text-text-muted mt-0.5">
              How thumbnail candidates are scored during scene analysis.
              AI-assisted mode uses vision to detect artist visibility, composition quality, and artifacts.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={draft.scene_analysis_mode ?? settings.scene_analysis_mode}
              onChange={(e) => {
                const val = e.target.value;
                setDraft({ ...draft, scene_analysis_mode: val });
                save({ scene_analysis_mode: val });
              }}
              className="input-field w-36 text-sm"
              disabled={!providerEnabled || updateMutation.isPending}
            >
              <option value="heuristic">Heuristic</option>
              <option value="ai_assisted">AI-Assisted</option>
            </select>
          </div>
        </div>
      </div>

      {/* ── AcoustID / Fingerprinting ── */}
      <div className="border-t border-white/5 pt-4">
        <div className="flex flex-col sm:flex-row sm:items-start gap-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <Fingerprint size={16} className="text-cyan-400" />
              <label className="text-sm font-medium text-text-primary">AcoustID API Key</label>
            </div>
            <p className="text-xs text-text-muted mt-0.5">
              Optional. Enables audio fingerprint identification via Chromaprint/AcoustID.{" "}
              {settings.acoustid_api_key_set && (
                <span className="text-green-400">Key configured</span>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="password"
              value={acoustidKey}
              onChange={(e) => setAcoustidKey(e.target.value)}
              className="input-field w-48 text-sm"
              placeholder={settings.acoustid_api_key_set ? "••••••••" : "Enter key"}
            />
            {acoustidKey && (
              <button
                onClick={() => {
                  save({ acoustid_api_key: acoustidKey });
                  setAcoustidKey("");
                }}
                className="btn-primary btn-sm"
                disabled={updateMutation.isPending}
              >
                <Save size={14} />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── AI Prompt Templates ── */}
      <AIPromptSection />
    </div>
  );
}

/* ── Reusable sub-components ── */

function APIKeyRow({
  label,
  isSet,
  value,
  onChange,
  onSave,
  isPending,
}: {
  label: string;
  isSet: boolean;
  value: string;
  onChange: (v: string) => void;
  onSave: () => void;
  isPending: boolean;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-2">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <Key size={16} className="text-text-muted" />
          <label className="text-sm font-medium text-text-primary">{label}</label>
        </div>
        <p className="text-xs text-text-muted mt-0.5">
          {isSet ? "Key is configured" : "Not configured"}{" "}
          <span className={`inline-block w-2 h-2 rounded-full ${isSet ? "bg-green-500" : "bg-red-500"}`} />
        </p>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="input-field w-64 text-sm"
          placeholder={isSet ? "••••••••" : "Enter API key"}
        />
        {value && (
          <button onClick={onSave} className="btn-primary btn-sm" disabled={isPending}>
            <Save size={14} />
          </button>
        )}
      </div>
    </div>
  );
}

function ModelSelect({
  label,
  description,
  value,
  options,
  onChange,
  isPending,
}: {
  label: string;
  description: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  isPending: boolean;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-2 ml-4">
      <div className="flex-1 min-w-0">
        <label className="text-sm font-medium text-text-primary">{label}</label>
        <p className="text-xs text-text-muted mt-0.5">{description}</p>
      </div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="input-field w-52 text-sm"
        disabled={isPending}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </div>
  );
}

/* ── AI Prompt Template Editor ── */

const PROMPT_FIELDS = [
  {
    key: "system_prompt" as const,
    defaultKey: "is_default_system" as const,
    label: "System Prompt",
    description: "The system message sent to the AI before the enrichment prompt. Sets the AI's role and behaviour.",
    rows: 4,
  },
  {
    key: "enrichment_prompt" as const,
    defaultKey: "is_default_enrichment" as const,
    label: "Enrichment Prompt",
    description: "Main template for metadata enrichment requests. Placeholders: {artist}, {title}, {album}, {year}, {genres}, {plot}, {file_context}, {platform_context}, {mismatch_section}, {max_plot_length}",
    rows: 16,
  },
  {
    key: "review_prompt" as const,
    defaultKey: "is_default_review" as const,
    label: "Description Review Prompt",
    description: "Template for description-only review mode. Placeholders: {artist}, {title}, {file_context}, {platform_context}, {plot}, {max_plot_length}",
    rows: 12,
  },
];

function AIPromptSection() {
  const { toast } = useToast();
  const { data: prompts, isLoading, isError } = useAIPrompts();
  const updateMutation = useAIUpdatePrompts();
  const [expanded, setExpanded] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [editingField, setEditingField] = useState<string | null>(null);

  // Sync drafts when prompts load
  useEffect(() => {
    if (prompts) {
      setDrafts({});
      setEditingField(null);
    }
  }, [prompts]);

  if (isLoading) return <Skeleton className="h-8" />;
  if (isError || !prompts) return null;

  const hasDraft = (key: string) => drafts[key] !== undefined && drafts[key] !== prompts[key as keyof typeof prompts];

  const savePrompt = (key: string) => {
    const value = drafts[key];
    if (value === undefined) return;
    updateMutation.mutate(
      { [key]: value } as AIPromptSettingsUpdate,
      {
        onSuccess: () => {
          toast({ type: "success", title: "Prompt template saved" });
          setDrafts((d) => { const n = { ...d }; delete n[key]; return n; });
          setEditingField(null);
        },
        onError: () => toast({ type: "error", title: "Failed to save prompt" }),
      },
    );
  };

  const resetPrompt = (key: string) => {
    updateMutation.mutate(
      { [key]: "" } as AIPromptSettingsUpdate,
      {
        onSuccess: () => {
          toast({ type: "success", title: "Prompt reset to default" });
          setDrafts((d) => { const n = { ...d }; delete n[key]; return n; });
          setEditingField(null);
        },
        onError: () => toast({ type: "error", title: "Failed to reset prompt" }),
      },
    );
  };

  return (
    <div className="border-t border-white/5 pt-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full text-left"
      >
        {expanded ? <ChevronDown size={16} className="text-text-muted" /> : <ChevronRight size={16} className="text-text-muted" />}
        <FileText size={16} className="text-amber-400" />
        <h4 className="text-sm font-medium text-text-primary">AI Prompt Templates</h4>
        <span className="text-xs text-text-muted ml-1">Customise prompts sent to the AI</span>
      </button>

      {expanded && (
        <div className="mt-4 space-y-5">
          {PROMPT_FIELDS.map((field) => {
            const isDefault = prompts[field.defaultKey];
            const currentValue = drafts[field.key] ?? prompts[field.key];
            const isEditing = editingField === field.key;
            const changed = hasDraft(field.key);

            return (
              <div key={field.key} className="space-y-2">
                <div className="flex items-center justify-between">
                  <div>
                    <label className="text-sm font-medium text-text-primary">{field.label}</label>
                    {isDefault && (
                      <span className="ml-2 text-xs text-text-muted bg-surface-light px-1.5 py-0.5 rounded">default</span>
                    )}
                    {!isDefault && (
                      <span className="ml-2 text-xs text-amber-400 bg-amber-400/10 px-1.5 py-0.5 rounded">customised</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {!isDefault && (
                      <Tooltip content="Reset to default prompt">
                      <button
                        onClick={() => resetPrompt(field.key)}
                        className="btn-secondary btn-sm flex items-center gap-1 text-xs"
                        disabled={updateMutation.isPending}
                      >
                        <RotateCcw size={12} />
                        Reset
                      </button>
                      </Tooltip>
                    )}
                    {changed && (
                      <button
                        onClick={() => savePrompt(field.key)}
                        className="btn-primary btn-sm flex items-center gap-1 text-xs"
                        disabled={updateMutation.isPending}
                      >
                        <Save size={12} />
                        Save
                      </button>
                    )}
                  </div>
                </div>
                <p className="text-xs text-text-muted leading-relaxed">{field.description}</p>
                {isEditing ? (
                  <textarea
                    value={currentValue}
                    onChange={(e) => setDrafts((d) => ({ ...d, [field.key]: e.target.value }))}
                    onBlur={() => { if (!changed) setEditingField(null); }}
                    rows={field.rows}
                    className="input-field w-full text-xs font-mono leading-relaxed resize-y"
                    autoFocus
                  />
                ) : (
                  <div
                    onClick={() => setEditingField(field.key)}
                    className="input-field w-full text-xs font-mono leading-relaxed cursor-pointer max-h-32 overflow-y-auto whitespace-pre-wrap"
                  >
                    {currentValue.slice(0, 500)}{currentValue.length > 500 ? "…" : ""}
                    <span className="block text-text-muted text-[10px] mt-1 italic">Click to edit</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
