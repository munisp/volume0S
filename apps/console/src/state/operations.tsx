import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useSession, errorText } from './session-context'
import { OperationsContext, type FeedState } from './operations-context'
import { fromCampaign, fromEngagement, fromOfferJob, fromStudio, type WorkItem, type WorkKind } from '../lib/workflows'

const KINDS: WorkKind[] = ['engagement', 'offer-job', 'studio', 'campaign']
const idle = (): FeedState => ({ load: 'idle', error: null, items: [] })

export function OperationsProvider({ children }: { children: ReactNode }) {
  const { api, user, tenant } = useSession()
  const [feeds, setFeeds] = useState<Record<WorkKind, FeedState>>({ engagement: idle(), 'offer-job': idle(), studio: idle(), campaign: idle() })
  const [refreshing, setRefreshing] = useState(false)
  const [refreshedAt, setRefreshedAt] = useState<Date | null>(null)
  const generation = useRef(0)
  const enabled = Boolean(user && tenant && api.orchestratorConfigured)

  const refresh = useCallback(async () => {
    if (!enabled) return
    const run = ++generation.current
    setRefreshing(true)
    setFeeds(current => Object.fromEntries(KINDS.map(kind => [kind, { ...current[kind], load: current[kind].load === 'idle' ? 'loading' : current[kind].load }])) as Record<WorkKind, FeedState>)
    const loaders: Record<WorkKind, () => Promise<WorkItem[]>> = {
      engagement: async () => (await api.engagements()).engagements.map(fromEngagement),
      'offer-job': async () => (await api.offerJobs()).jobs.map(fromOfferJob),
      studio: async () => (await api.studioPacks()).packs.map(fromStudio),
      campaign: async () => (await api.campaigns()).campaigns.map(fromCampaign),
    }
    const results = await Promise.allSettled(KINDS.map(kind => loaders[kind]()))
    if (run !== generation.current) return
    setFeeds(current => {
      const next = { ...current }
      KINDS.forEach((kind, index) => {
        const result = results[index]
        // A failed refresh keeps the last good list visible but marks the feed as failing.
        next[kind] = result.status === 'fulfilled'
          ? { load: 'ok', error: null, items: result.value }
          : { load: 'error', error: errorText(result.reason), items: current[kind].items }
      })
      return next
    })
    setRefreshedAt(new Date())
    setRefreshing(false)
  }, [api, enabled])

  useEffect(() => {
    if (!enabled) return
    const first = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible' && navigator.onLine) void refresh() }, 30_000)
    return () => { window.clearTimeout(first); window.clearInterval(timer); generation.current += 1 }
  }, [enabled, refresh])

  const items = useMemo(() => KINDS.flatMap(kind => feeds[kind].items), [feeds])
  return <OperationsContext.Provider value={{ feeds, items, refreshing, refreshedAt, refresh }}>{children}</OperationsContext.Provider>
}
