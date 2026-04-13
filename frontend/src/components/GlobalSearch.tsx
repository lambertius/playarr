import { Search, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

/** Routes where search stays on the current page instead of navigating to /library */
const FACET_ROUTES = ["/artists", "/albums", "/years", "/genres", "/ratings", "/quality", "/library"];

const DEBOUNCE_MS = 250;

export function GlobalSearch() {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Sync local state with URL search param when route changes
  useEffect(() => {
    const urlSearch = searchParams.get("search") ?? "";
    setQuery(urlSearch);
  }, [location.pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  const isFacetRoute = FACET_ROUTES.some((r) => location.pathname.endsWith(r));

  const applySearch = useCallback((value: string) => {
    if (isFacetRoute) {
      const next = new URLSearchParams(searchParams);
      if (value) {
        next.set("search", value);
      } else {
        next.delete("search");
      }
      setSearchParams(next, { replace: true });
    } else {
      if (value) {
        navigate(`/library?search=${encodeURIComponent(value)}`);
      }
    }
  }, [isFacetRoute, searchParams, setSearchParams, navigate]);

  // Live filtering: debounce on each keystroke when on a facet route
  const handleChange = useCallback((value: string) => {
    setQuery(value);
    if (isFacetRoute) {
      clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => applySearch(value.trim()), DEBOUNCE_MS);
    }
  }, [isFacetRoute, applySearch]);

  // Cleanup debounce on unmount
  useEffect(() => () => clearTimeout(debounceRef.current), []);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    clearTimeout(debounceRef.current);
    applySearch(query.trim());
  };

  const handleClear = () => {
    setQuery("");
    clearTimeout(debounceRef.current);
    applySearch("");
  };

  return (
    <form onSubmit={handleSubmit} className="relative flex-1 max-w-md">
      <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
      <input
        type="text"
        value={query}
        onChange={(e) => handleChange(e.target.value)}
        placeholder="Search library..."
        className="input-field pl-9 pr-8 py-1.5"
        aria-label="Search library"
      />
      {query && (
        <button
          type="button"
          onClick={handleClear}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary"
          aria-label="Clear search"
        >
          <X size={14} />
        </button>
      )}
    </form>
  );
}
