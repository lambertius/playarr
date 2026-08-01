import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export class RouteErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Route render failed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="m-6 rounded-lg border border-red-500/30 bg-red-500/5 p-6 text-text-primary" role="alert">
        <div className="flex items-center gap-2 text-red-400"><AlertTriangle size={20} /><h1 className="font-semibold">This page could not be displayed</h1></div>
        <p className="mt-2 text-sm text-text-secondary">Playback has been left running where possible. Reload this route to try again.</p>
        <details className="mt-3 text-xs text-text-muted"><summary className="cursor-pointer">Error details</summary><pre className="mt-2 whitespace-pre-wrap">{this.state.error.message}</pre></details>
        <button className="btn-primary mt-4" onClick={() => window.location.reload()}><RefreshCw size={14} /> Reload page</button>
      </div>
    );
  }
}
