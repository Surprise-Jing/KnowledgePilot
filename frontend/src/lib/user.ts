// The current demo user, sent as X-User-Id. Storage access can throw (private mode, blocked
// site data), so every read and write is guarded and falls back to in-memory state.

export const PRESET_USERS = ['demo', 'alice', 'bob'] as const
export const DEFAULT_USER = 'demo'
export const USER_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/

const CURRENT_KEY = 'kp.userId'
const KNOWN_KEY = 'kp.knownUsers'

let memoryUser: string | null = null

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    // Storage unavailable: behave as if nothing was saved.
    return null
  }
}

function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Storage unavailable: the in-memory value still applies for this page load.
  }
}

export function isValidUserId(value: string): boolean {
  return USER_ID_PATTERN.test(value)
}

export function getCurrentUser(): string {
  if (memoryUser) return memoryUser
  const stored = readStorage(CURRENT_KEY)
  memoryUser = stored && isValidUserId(stored) ? stored : DEFAULT_USER
  return memoryUser
}

export function setCurrentUser(userId: string): void {
  if (!isValidUserId(userId)) {
    throw new Error('用户 ID 只能包含字母、数字、下划线和连字符，长度 1–64')
  }
  memoryUser = userId
  writeStorage(CURRENT_KEY, userId)
  const known = getKnownUsers()
  if (!known.includes(userId)) {
    writeStorage(KNOWN_KEY, JSON.stringify([...known.filter((u) => !isPreset(u)), userId]))
  }
}

function isPreset(userId: string): boolean {
  return (PRESET_USERS as readonly string[]).includes(userId)
}

/** Preset users followed by custom users entered earlier in this browser. */
export function getKnownUsers(): string[] {
  const raw = readStorage(KNOWN_KEY)
  let custom: string[] = []
  if (raw) {
    try {
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        custom = parsed.filter((u): u is string => typeof u === 'string' && isValidUserId(u))
      }
    } catch {
      // Corrupt entry: ignore it and start over with the presets.
      custom = []
    }
  }
  return [...PRESET_USERS, ...custom.filter((u) => !isPreset(u))]
}
