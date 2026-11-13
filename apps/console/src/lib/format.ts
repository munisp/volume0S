/** Money, dates and relative times, formatted for Nigeria (en-NG). */
export function money(minor: number | string | null | undefined, currency = 'NGN'): string {
  if (minor === null || minor === undefined || minor === '') return '—'
  const value = typeof minor === 'string' ? Number(minor) : minor
  if (!Number.isFinite(value)) return '—'
  return new Intl.NumberFormat('en-NG', { style: 'currency', currency, maximumFractionDigits: value % 100 === 0 ? 0 : 2 }).format(value / 100)
}

export function dateTime(value: string | number | Date | null | undefined): string {
  if (!value) return '—'
  const date = value instanceof Date ? value : new Date(typeof value === 'number' ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat('en-NG', { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function ago(value: string | null | undefined, now = Date.now()): string {
  if (!value) return '—'
  const then = new Date(value).getTime()
  if (Number.isNaN(then)) return '—'
  const minutes = Math.round((now - then) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h ago`
  return `${Math.round(hours / 24)} days ago`
}

