import { cn } from "@/lib/utils";

interface SkeletonProps {
  className?: string;
}

export function Skeleton({ className }: SkeletonProps) {
  return <div className={cn("skeleton", className)} />;
}

/** Full-page centered empty state. */
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      {icon && <div className="mb-4 text-text-muted">{icon}</div>}
      <h3 className="text-lg font-semibold text-text-primary">{title}</h3>
      {description && <p className="mt-1 text-sm text-text-secondary max-w-sm">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Error state with retry. */
export function ErrorState({
  message = "Something went wrong",
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="mb-2 text-4xl">⚠️</div>
      <h3 className="text-lg font-semibold text-text-primary">Error</h3>
      <p className="mt-1 text-sm text-text-secondary">{message}</p>
      {onRetry && (
        <button onClick={onRetry} className="btn-primary btn-sm mt-4">
          Retry
        </button>
      )}
    </div>
  );
}

/** Grid skeleton for library loading state. */
export function LibrarySkeleton({ count = 12 }: { count?: number }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,200px)] gap-4">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="space-y-2">
          <Skeleton className="aspect-video w-full rounded-lg" />
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
        </div>
      ))}
    </div>
  );
}
