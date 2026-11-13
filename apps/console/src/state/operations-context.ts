import { createContext, useContext } from 'react'
import type { WorkItem, WorkKind } from '../lib/workflows'

/**
 * Every piece of work in this business, from the four workflow feeds, refreshed every 30 seconds while visible.
 * Each feed keeps its own health: one failing feed never blanks the others, and is never shown as "0".
 */
export type FeedState = { load: 'idle' | 'loading' | 'ok' | 'error'; error: string | null; items: WorkItem[] }
export type Operations = {
  feeds: Record<WorkKind, FeedState>
  items: WorkItem[]
  refreshing: boolean
  refreshedAt: Date | null
  refresh: () => Promise<void>
}

export const OperationsContext = createContext<Operations | null>(null)

export function useOperations(): Operations {
  const value = useContext(OperationsContext)
  if (!value) throw new Error('useOperations outside OperationsProvider')
  return value
}

