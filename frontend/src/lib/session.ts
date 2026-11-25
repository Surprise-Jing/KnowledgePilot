// Lets the API client tell the auth layer that the session is gone (401 unauthenticated)
// without importing React state into the request code.

type Listener = () => void

const listeners = new Set<Listener>()

export function onSessionExpired(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function notifySessionExpired(): void {
  for (const listener of listeners) listener()
}
