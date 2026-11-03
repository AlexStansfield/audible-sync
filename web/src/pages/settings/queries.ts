/**
 * What the Settings screen reads and changes. The settings are `["settings"]`; a save
 * puts the service's answer there, so the form's "saved" side is always what is stored.
 */
import { useIsMutating, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "../../api/client";
import { keys, SETTINGS_KEY, settingsQuery } from "../../api/queries";
import type { Settings, SettingsUpdate } from "./form";

/** Every settings save, the form's and debug logging's: one at a time, see `useSavingSettings` */
const SAVE_KEY = ["settings", "save"] as const;
const TOKEN_KEY = ["settings", "api-token"] as const;
const SYSTEM_KEY = ["system"] as const;

export function useSettings() {
  return useQuery(settingsQuery);
}

/**
 * Whether a settings save is in flight. The service writes every setting on each save,
 * so two at once could each put back what the other changed: the other control waits.
 */
export function useSavingSettings(): boolean {
  return useIsMutating({ mutationKey: SAVE_KEY }) > 0;
}

/**
 * `PUT /api/settings` with the changed fields. A saved schedule moves the next run and
 * a saved limit the Up next list, so the status and the queue are read again - except
 * after debug logging alone, which changes neither.
 */
export function useSaveSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationKey: SAVE_KEY,
    mutationFn: (update: SettingsUpdate) => unwrap(api.PUT("/api/settings", { body: update })),
    onSuccess: (saved: Settings, update) => {
      client.setQueryData(SETTINGS_KEY, saved);
      if (Object.keys(update).every((key) => key === "debug")) return;
      void client.invalidateQueries({ queryKey: keys.status });
      void client.invalidateQueries({ queryKey: ["queue"] });
    },
  });
}

/** `POST /api/settings/webhook/test` with the URL in the field. */
export function useWebhookTest() {
  return useMutation({
    mutationFn: (url: string) => unwrap(api.POST("/api/settings/webhook/test", { body: { url } })),
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
