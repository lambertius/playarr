import { Search } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

export function GlobalSearch() {
  const [query, setQuery] = useState("");
  const navigate = useNavigate();

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      navigate(`/library?search=${encodeURIComponent(query.trim())}`);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="relative flex-1 max-w-md">
      <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search library..."
        className="input-field pl-9 py-1.5"
        aria-label="Search library"
      />
    </form>
  );
}
