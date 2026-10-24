import { useEffect, useSyncExternalStore } from "react";

type Feedback = { tone: "success" | "error"; message: string } | null;

interface FormSnapshot<T> {
  value: T;
  dirty: boolean;
  pending: boolean;
  feedback: Feedback;
}

interface SaveOptions<T> {
  success: string;
  failure: string;
  savedValue?: (submitted: T) => T;
}

function same<T>(left: T, right: T): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

// These stores live for the renderer session, so navigating away does not
// discard an unfinished form or allow another submission of a pending save.
export function createSessionForm<T>(initial: T) {
  let baseline = initial;
  let serverValue = initial;
  let snapshot: FormSnapshot<T> = { value: initial, dirty: false, pending: false, feedback: null };
  const listeners = new Set<() => void>();
  const publish = (next: FormSnapshot<T>) => {
    snapshot = next;
    listeners.forEach((listener) => listener());
  };
  return {
    getSnapshot: () => snapshot,
    subscribe: (listener: () => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    syncServer: (next: T) => {
      if (same(serverValue, next)) return;
      serverValue = next;
      if (snapshot.pending) return;
      baseline = next;
      const value = snapshot.dirty ? snapshot.value : next;
      publish({ ...snapshot, value, dirty: !same(value, baseline) });
    },
    setValue: (next: T | ((current: T) => T)) => {
      const value = typeof next === "function" ? (next as (current: T) => T)(snapshot.value) : next;
      publish({ ...snapshot, value, dirty: !same(value, baseline), feedback: null });
    },
    showError: (message: string) => publish({ ...snapshot, feedback: { tone: "error", message } }),
    submit: async (send: (submitted: T) => Promise<boolean>, options: SaveOptions<T>) => {
      if (snapshot.pending) return false;
      const submitted = snapshot.value;
      const serverAtStart = serverValue;
      publish({ ...snapshot, pending: true, feedback: null });
      let saved = false;
      try {
        saved = await send(submitted);
      } catch {
        // Keep the local draft even when a caller rejects instead of returning false.
      }
      if (saved) {
        baseline = !same(serverAtStart, serverValue) ? serverValue : options.savedValue?.(submitted) ?? submitted;
        const value = same(snapshot.value, submitted) ? baseline : snapshot.value;
        publish({ value, dirty: !same(value, baseline), pending: false, feedback: { tone: "success", message: options.success } });
      } else {
        baseline = serverValue;
        publish({ ...snapshot, dirty: !same(snapshot.value, baseline), pending: false, feedback: { tone: "error", message: options.failure } });
      }
      return saved;
    },
  };
}

export function useSessionForm<T>(form: ReturnType<typeof createSessionForm<T>>, serverValue?: T) {
  const snapshot = useSyncExternalStore(form.subscribe, form.getSnapshot, form.getSnapshot);
  useEffect(() => {
    if (serverValue !== undefined) form.syncServer(serverValue);
  }, [form, serverValue]);
  return { ...snapshot, setValue: form.setValue, submit: form.submit, showError: form.showError };
}
