export type Limit = { kind: string; percent: number; resetsAt?: string }

export type Quota = {
  limits: Limit[]
  contextPercent?: number
  costUsd?: number
}

export type Local = {
  todayTokens: number
  todayRequests: number
  sessions: number
  at: number
}

// Prompt tokens summed over this session's main-thread turns: what was read
// from the cache against everything sent.
export type Cache = { read: number; total: number; lastRead: number; lastTotal: number }

declare module 'claude-code' {
  interface PluginState {
    ccquota: { quota: Quota | null; local: Local | null; cache: Cache | null; isHidden: boolean }
  }
}
