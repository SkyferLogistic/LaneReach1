/**
 * Confirmation dialogs and a delete helper used by every screen with a delete button.
 *
 * `useConfirm()` returns an element to render and an `ask()` that resolves true or false.
 * Irreversible, wide deletes (erasing a contact) can require typing a word first.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { QueryKey } from "@tanstack/react-query";
import { useCallback, useRef, useState } from "react";
import type { ReactNode } from "react";
import { describeError } from "../api/client";
import { Icon } from "./icons";
import { Button, Modal, useToast } from "./ui";
import { useSession } from "../auth/session";
import { roleNames } from "../lib/lastSignIn";

export interface ConfirmOptions {
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  /** The person must type this (for example the contact's email) to confirm. */
  typeToConfirm?: string;
}

export function useConfirm(): [ReactNode, (options: ConfirmOptions) => Promise<boolean>] {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const [typed, setTyped] = useState("");
  const resolver = useRef<((value: boolean) => void) | null>(null);
  const ask = useCallback((next: ConfirmOptions) => {
    setTyped("");
    setOptions(next);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);
  const close = (value: boolean) => {
    resolver.current?.(value);
    resolver.current = null;
    setOptions(null);
  };
  const ready = !options?.typeToConfirm || typed.trim().toLowerCase() === options.typeToConfirm.toLowerCase();
  const element = options ? (
    <Modal title={options.title} onClose={() => close(false)}>
      <div className="stack">
        <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
          {options.danger !== false && (
            <span className="confirm-icon" aria-hidden>
              <Icon name="trash" />
            </span>
          )}
          <div className="small" style={{ lineHeight: 1.55 }}>{options.message}</div>
        </div>
        {options.typeToConfirm && (
          <label className="field">
            <span>
              Type <strong>{options.typeToConfirm}</strong> to confirm
            </span>
            <input className="input" value={typed} onChange={(event) => setTyped(event.target.value)} autoFocus />
          </label>
        )}
        <div className="row" style={{ justifyContent: "flex-end", gap: 8 }}>
          <Button variant="ghost" onClick={() => close(false)}>Cancel</Button>
          <Button variant={options.danger === false ? "primary" : "danger"} disabled={!ready} onClick={() => close(true)}>
            {options.confirmLabel ?? "Delete"}
          </Button>
        </div>
      </div>
    </Modal>
  ) : null;
  return [element, ask];
}

/**
 * A delete mutation with confirmation, a toast and cache invalidation. A 409 answer (the
 * record is still in use) is shown as the server's explanation.
 */
/**
 * Whether the signed-in person may use an action, and a plain explanation when not.
 *
 * Buttons stay visible for every role (hiding them made people think the feature was missing);
 * a locked button explains who can use it instead of doing nothing.
 */
export function usePermission(scope: string, who: string): { allowed: boolean; reason: string; explain: () => void } {
  const { can, session } = useSession();
  const toast = useToast();
  const allowed = can(scope);
  const current = roleNames(session?.roles ?? []);
  const reason = `Only ${who} can do this. You are signed in as ${current}: sign out and choose a role that can.`;
  return { allowed, reason, explain: () => toast(reason, "error") };
}

export function useDelete<T = void>({
  run,
  success,
  invalidate = [],
  onDone,
}: {
  run: (target: T) => Promise<unknown>;
  success: string;
  invalidate?: QueryKey[];
  onDone?: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      toast(success);
      for (const key of invalidate) void queryClient.invalidateQueries({ queryKey: key });
      onDone?.();
    },
    onError: (error) => toast(describeError(error), "error"),
  });
}
