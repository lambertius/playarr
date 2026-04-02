import { useNewVideosSettings, useNewVideosUpdateSettings } from "@/hooks/queries";
import { Skeleton } from "@/components/Feedback";

/* ── Setting definition for rendering ── */
interface NVSettingDef {
  key: string;
  label: string;
  description: string;
  type: "bool" | "int" | "float" | "string";
  min?: number;
  max?: number;
  step?: number;
}

const SECTIONS: { title: string; settings: NVSettingDef[] }[] = [
  {
    title: "Feed Behaviour",
    settings: [
      { key: "nv_enabled", label: "Enable Discovery", description: "Turn the New Videos discovery system on or off.", type: "bool" },
      { key: "nv_videos_per_category", label: "Videos Per Category", description: "Default number of videos shown in each category row.", type: "int", min: 5, max: 50 },
      { key: "nv_refresh_interval_minutes", label: "Refresh Interval (min)", description: "How often cached suggestions expire before the next refresh.", type: "int", min: 30, max: 1440, step: 30 },
      { key: "nv_auto_refresh_on_startup", label: "Auto-Refresh on Startup", description: "Automatically refresh suggestions when the app starts.", type: "bool" },
      { key: "nv_include_temp_dismissed_after_refresh", label: "Restore Dismissed on Refresh", description: "Temporarily dismissed videos reappear after a feed refresh.", type: "bool" },
    ],
  },
  {
    title: "Recommendation Behaviour",
    settings: [
      { key: "nv_enable_ai_ranking", label: "AI-Assisted Ranking", description: "Use AI enrichment for smarter ranking (requires AI settings configured).", type: "bool" },
      { key: "nv_enable_trusted_source_filtering", label: "Trusted Source Filtering", description: "Prefer VEVO and official artist channels; penalise unofficial sources.", type: "bool" },
      { key: "nv_min_trust_threshold", label: "Minimum Trust Score", description: "Videos below this trust score are excluded. Range 0.0–1.0.", type: "float", min: 0, max: 1, step: 0.05 },
      { key: "nv_allow_unofficial_fallback", label: "Allow Unofficial Fallback", description: "Show unofficial videos if no trusted source is available.", type: "bool" },
      { key: "nv_preferred_providers", label: "Preferred Providers", description: "Comma-separated list of providers to search (e.g. youtube).", type: "string" },
    ],
  },
  {
    title: "Artist Recommendations",
    settings: [
      { key: "nv_min_owned_for_artist_rec", label: "Min Owned for Artist Rec", description: "Minimum videos you must own by an artist before recommending more.", type: "int", min: 1, max: 20 },
      { key: "nv_max_recs_per_artist", label: "Max Recs Per Artist", description: "Maximum recommendations per artist per category.", type: "int", min: 1, max: 20 },
    ],
  },
  {
    title: "Preference-Based",
    settings: [
      { key: "nv_use_ratings", label: "Use Ratings", description: "Factor your star ratings into recommendation scoring.", type: "bool" },
      { key: "nv_use_genre_similarity", label: "Use Genre Similarity", description: "Recommend videos from genres you listen to most.", type: "bool" },
      { key: "nv_use_artist_similarity", label: "Use Artist Similarity", description: "Recommend videos from similar artists.", type: "bool" },
    ],
  },
  {
    title: "Cart Behaviour",
    settings: [
      { key: "nv_persist_cart", label: "Persist Cart", description: "Keep cart items across refreshes and sessions.", type: "bool" },
      { key: "nv_auto_clear_cart", label: "Auto-Clear After Import", description: "Clear the cart automatically after a successful import.", type: "bool" },
    ],
  },
  {
    title: "Category Sizes",
    settings: [
      { key: "nv_famous_count", label: "Famous Videos", description: "Number of iconic music videos to show.", type: "int", min: 5, max: 50 },
      { key: "nv_popular_count", label: "Popular Videos", description: "Number of popular/trending videos to show.", type: "int", min: 5, max: 50 },
      { key: "nv_rising_count", label: "Rising Videos", description: "Number of rising/new releases to show.", type: "int", min: 5, max: 50 },
      { key: "nv_new_count", label: "New Videos", description: "Number of brand new videos to show.", type: "int", min: 5, max: 50 },
    ],
  },
];

export function NewVideosSettings() {
  const { data: settings, isLoading } = useNewVideosSettings();
  const updateMut = useNewVideosUpdateSettings();

  if (isLoading || !settings) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-12 rounded-lg" />
        ))}
      </div>
    );
  }

  const update = (key: string, value: string | number | boolean) => {
    updateMut.mutate([{ key, value: String(value) }]);
  };

  return (
    <div className="space-y-6">
      <p className="text-xs text-text-muted leading-relaxed">
        Configure how the New Videos discovery feed generates recommendations, scores trust, and manages your cart.
      </p>

      {SECTIONS.map((section) => (
        <div key={section.title}>
          <h4 className="text-xs font-semibold uppercase tracking-wider text-text-muted mb-3">
            {section.title}
          </h4>
          <div className="space-y-4 pl-2 border-l-2 border-border">
            {section.settings.map((def) => (
              <SettingRow key={def.key} def={def} value={settings[def.key as keyof typeof settings]} onChange={update} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ── Individual setting row ── */

function SettingRow({
  def,
  value,
  onChange,
}: {
  def: NVSettingDef;
  value: unknown;
  onChange: (key: string, value: string | number | boolean) => void;
}) {
  if (def.type === "bool") {
    const checked = value === true || value === "true";
    return (
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">{def.label}</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">{def.description}</p>
        </div>
        <div className="shrink-0 sm:pt-0.5">
          <button
            type="button"
            role="switch"
            aria-checked={checked}
            onClick={() => onChange(def.key, !checked)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              checked ? "bg-accent" : "bg-border"
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                checked ? "translate-x-6" : "translate-x-1"
              }`}
            />
          </button>
        </div>
      </div>
    );
  }

  if (def.type === "int" || def.type === "float") {
    const numVal = Number(value) || 0;
    return (
      <div className="flex flex-col sm:flex-row sm:items-start gap-2">
        <div className="flex-1 min-w-0">
          <label className="text-sm font-medium text-text-primary">{def.label}</label>
          <p className="text-xs text-text-muted mt-0.5 leading-relaxed">{def.description}</p>
        </div>
        <div className="flex items-center gap-3 shrink-0 sm:pt-0.5">
          <input
            type="range"
            min={def.min ?? 0}
            max={def.max ?? 100}
            step={def.step ?? (def.type === "float" ? 0.05 : 1)}
            value={numVal}
            onChange={(e) => {
              const v = def.type === "float" ? parseFloat(e.target.value) : parseInt(e.target.value, 10);
              onChange(def.key, v);
            }}
            className="w-32 accent-accent"
          />
          <span className="text-sm text-text-secondary w-12 text-right tabular-nums">
            {def.type === "float" ? numVal.toFixed(2) : numVal}
          </span>
        </div>
      </div>
    );
  }

  // string
  const strVal = String(value ?? "");
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-2">
      <div className="flex-1 min-w-0">
        <label className="text-sm font-medium text-text-primary">{def.label}</label>
        <p className="text-xs text-text-muted mt-0.5 leading-relaxed">{def.description}</p>
      </div>
      <div className="shrink-0 sm:pt-0.5">
        <input
          type="text"
          value={strVal}
          onChange={(e) => onChange(def.key, e.target.value)}
          className="w-40 px-2 py-1 rounded bg-bg-secondary border border-border text-sm text-text-primary"
        />
      </div>
    </div>
  );
}
