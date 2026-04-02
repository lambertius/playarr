/**
 * useJobTelemetry — SSE hook for real-time job telemetry.
 *
 * Connects to /api/jobs/stream and pushes updates into a React state.
 * Falls back to polling /api/jobs/telemetry every 2s if SSE fails.
 */
import { useEffect, useRef, useState, useCallback } from "react";
import type { TelemetrySnapshot, JobTelemetry } from "@/types";
import { jobsApi } from "@/lib/api";

export function useJobTelemetry() {
  const [telemetry, setTelemetry] = useState<TelemetrySnapshot>({});
  const [connected, setConnected] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const fallbackRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let disposed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function stopPolling() {
      if (fallbackRef.current) {
        clearInterval(fallbackRef.current);
        fallbackRef.current = null;
      }
    }

    function startPolling() {
      if (fallbackRef.current) return;
      fallbackRef.current = setInterval(async () => {
        try {
          const data = await jobsApi.telemetry();
          if (!disposed) setTelemetry(data);
        } catch {
          // Ignore polling errors
        }
      }, 2000);
    }

    function connectSSE() {
      if (disposed) return;
      // Clean up existing
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }

      const es = new EventSource("/api/jobs/stream");
      eventSourceRef.current = es;

      es.addEventListener("telemetry", (event) => {
        try {
          const data = JSON.parse(event.data) as TelemetrySnapshot;
          if (!disposed) {
            setTelemetry(data);
            setConnected(true);
          }
        } catch {
          // Ignore parse errors
        }
      });

      es.addEventListener("heartbeat", () => {
        if (!disposed) setConnected(true);
      });

      es.onerror = () => {
        if (disposed) return;
        setConnected(false);
        es.close();
        eventSourceRef.current = null;
        // Fall back to polling
        startPolling();
        // Try to reconnect SSE after 5s
        reconnectTimer = setTimeout(() => {
          if (!disposed && !eventSourceRef.current) {
            connectSSE();
          }
        }, 5000);
      };

      es.onopen = () => {
        if (!disposed) {
          setConnected(true);
          stopPolling();
        }
      };
    }

    connectSSE();

    return () => {
      disposed = true;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
      stopPolling();
      if (reconnectTimer) clearTimeout(reconnectTimer);
    };
  }, []);

  const getJobTelemetry = useCallback(
    (jobId: number): JobTelemetry | undefined => {
      return telemetry[String(jobId)];
    },
    [telemetry]
  );

  return { telemetry, connected, getJobTelemetry };
}
