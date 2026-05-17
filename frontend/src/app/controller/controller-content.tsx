"use client";

import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type DevLoopStatus = {
  running: boolean;
  current_repo?: string | null;
  current_task?: string | null;
  last_tick_at?: string | null;
};

type KanbanCard = {
  id: string;
  title: string;
  status: string;
  repo?: string | null;
};

type KanbanBoard = {
  columns: Record<string, KanbanCard[]>;
};

type FetchState<T> =
  | { tag: "idle" }
  | { tag: "loading" }
  | { tag: "ok"; value: T }
  | { tag: "error"; message: string }
  | { tag: "disabled" };

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (res.status === 404) {
    throw new Error("disabled");
  }
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export function ControllerContent() {
  const [status, setStatus] = useState<FetchState<DevLoopStatus>>({
    tag: "idle",
  });
  const [board, setBoard] = useState<FetchState<KanbanBoard>>({ tag: "idle" });
  const [actionInFlight, setActionInFlight] = useState<"start" | "stop" | null>(
    null,
  );

  const refreshStatus = useCallback(async () => {
    setStatus({ tag: "loading" });
    try {
      const value = await fetchJson<DevLoopStatus>(
        "/api/v1/controller/dev-loop/status",
      );
      setStatus({ tag: "ok", value });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setStatus(
        msg === "disabled"
          ? { tag: "disabled" }
          : { tag: "error", message: msg },
      );
    }
  }, []);

  const refreshBoard = useCallback(async () => {
    setBoard({ tag: "loading" });
    try {
      const value = await fetchJson<KanbanBoard>(
        "/api/v1/controller/kanban/board",
      );
      setBoard({ tag: "ok", value });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setBoard(
        msg === "disabled"
          ? { tag: "disabled" }
          : { tag: "error", message: msg },
      );
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
    void refreshBoard();
    const id = setInterval(() => {
      void refreshStatus();
    }, 10_000);
    return () => clearInterval(id);
  }, [refreshStatus, refreshBoard]);

  const handleStart = useCallback(async () => {
    setActionInFlight("start");
    try {
      await fetchJson("/api/v1/controller/dev-loop/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
    } catch {
      // swallow -- status refresh surfaces error state
    } finally {
      setActionInFlight(null);
      void refreshStatus();
    }
  }, [refreshStatus]);

  const handleStop = useCallback(async () => {
    setActionInFlight("stop");
    try {
      await fetchJson("/api/v1/controller/dev-loop/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
    } catch {
      // swallow -- status refresh surfaces error state
    } finally {
      setActionInFlight(null);
      void refreshStatus();
    }
  }, [refreshStatus]);

  return (
    <div className="flex flex-col gap-6 p-6">
      <header className="flex items-baseline justify-between">
        <div>
          <h1 className="text-2xl font-semibold">AI Dev Controller</h1>
          <p className="text-muted-foreground text-sm">
            Operator surface for the in-cluster dev loop.
          </p>
        </div>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Dev loop</CardTitle>
        </CardHeader>
        <CardContent>
          {status.tag === "disabled" && (
            <p className="text-muted-foreground text-sm">
              Controller proxy is disabled in this environment. Set{" "}
              <code className="bg-muted rounded px-1">
                DEER_FLOW_CONTROLLER_PROXY_ENABLED=1
              </code>{" "}
              on the gateway and redeploy to enable.
            </p>
          )}
          {status.tag === "loading" && (
            <p className="text-muted-foreground text-sm">Loading…</p>
          )}
          {status.tag === "error" && (
            <p className="text-destructive text-sm">
              Status fetch failed: {status.message}
            </p>
          )}
          {status.tag === "ok" && (
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-2">
                <Badge variant={status.value.running ? "default" : "secondary"}>
                  {status.value.running ? "running" : "idle"}
                </Badge>
                {status.value.current_repo && (
                  <span className="text-muted-foreground text-sm">
                    repo: <code>{status.value.current_repo}</code>
                  </span>
                )}
                {status.value.current_task && (
                  <span className="text-muted-foreground text-sm">
                    task: <code>{status.value.current_task}</code>
                  </span>
                )}
              </div>
              {status.value.last_tick_at && (
                <p className="text-muted-foreground text-xs">
                  Last tick: {status.value.last_tick_at}
                </p>
              )}
              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={() => void handleStart()}
                  disabled={actionInFlight !== null || status.value.running}
                >
                  {actionInFlight === "start" ? "Starting…" : "Start"}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void handleStop()}
                  disabled={actionInFlight !== null || !status.value.running}
                >
                  {actionInFlight === "stop" ? "Stopping…" : "Stop"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => void refreshStatus()}
                >
                  Refresh
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Kanban board</CardTitle>
        </CardHeader>
        <CardContent>
          {board.tag === "disabled" && (
            <p className="text-muted-foreground text-sm">
              Controller proxy disabled — board unavailable.
            </p>
          )}
          {board.tag === "loading" && (
            <p className="text-muted-foreground text-sm">Loading…</p>
          )}
          {board.tag === "error" && (
            <p className="text-destructive text-sm">
              Board fetch failed: {board.message}
            </p>
          )}
          {board.tag === "ok" && (
            <div className="grid gap-4 md:grid-cols-3">
              {Object.entries(board.value.columns ?? {}).map(
                ([column, cards]) => (
                  <div key={column} className="flex flex-col gap-2">
                    <h3 className="text-sm font-semibold tracking-wide uppercase">
                      {column}{" "}
                      <span className="text-muted-foreground">
                        ({cards.length})
                      </span>
                    </h3>
                    <ul className="flex flex-col gap-2">
                      {cards.map((card) => (
                        <li
                          key={card.id}
                          className="bg-card rounded border p-2 text-sm"
                        >
                          <div className="font-medium">{card.title}</div>
                          {card.repo && (
                            <div className="text-muted-foreground text-xs">
                              {card.repo}
                            </div>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                ),
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
