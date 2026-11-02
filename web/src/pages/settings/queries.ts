/**
 * What the Settings screen reads and changes. The settings are `["settings"]`; a save
 * puts the service's answer there, so the form's "saved" side is always what is stored.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { keys } from "../../api/queries";
import type { Settings, SettingsUpdate } from "./form";

export const SETTINGS_KEY = ["settings"] as const;
const TOKEN_KEY = ["settings", "api-token"] as const;
const SYSTEM_KEY = ["system"] as const;

export function useSettings() {
  return useQuery({ queryKey: SETTINGS_KEY, queryFn: () => unwrap(api.GET("/api/settings")) });
}

/**
 * `PUT /api/settings` with the changed fields. A saved schedule moves the next run and
 * a saved limit the Up next list, so the status and the queue are read again.
 */
export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (update: SettingsUpdate) => unwrap(api.PUT("/api/settings", { body: update })),
    onSuccess: (saved: Settings) => {
      client.setQueryData(SETTINGS_KEY, saved);
      void client.invalidateQueries({ queryKey: keys.status });
      void client.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}

/** `POST /api/settings/webhook/test` with the URL in the field (else the saved one). */
export function useWebhookTest() {
  return useMutation({
    mutationFn: (url: string) => unwrap(api.POST("/api/settings/webhook/test", { body: url ? { url } : {} })),
  });
}

export function useApiToken() {
  return useQuery({ queryKey: TOKEN_KEY, queryFn: () => unwrap(api.GET("/api/settings/api-token")) });
}

/** The new token comes back this once; the masked one is read again for its last four. */
export function useRegenerateToken() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/settings/api-token/regenerate")),
    onSuccess: () => void client.invalidateQueries({ queryKey: TOKEN_KEY }),
  });
}

export function useSystem() {
  return useQuery({ queryKey: SYSTEM_KEY, queryFn: () => unwrap(api.GET("/api/system")) });
}
