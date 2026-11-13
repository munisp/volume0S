/** Contact CSV import for campaigns: parse in the browser, send rows to the server (which validates again). */

export type ContactInput = {
  first_name: string
  email: string | null
  phone: string | null
  email_consent: boolean
  whatsapp_consent: boolean
}

export type ParsedContacts = { rows: ContactInput[]; skipped: number; consentColumns: boolean }

const YES = new Set(['yes', 'y', 'true', '1', 'opted in', 'opt-in', 'agreed'])

/** Splits one CSV line, honouring double-quoted fields with commas and "" escapes. */
export function splitCsvLine(line: string): string[] {
  const out: string[] = []
  let field = ''
  let quoted = false
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    if (quoted) {
      if (ch === '"' && line[i + 1] === '"') { field += '"'; i++ }
      else if (ch === '"') quoted = false
      else field += ch
    } else if (ch === '"') quoted = true
    else if (ch === ',') { out.push(field.trim()); field = '' }
    else field += ch
  }
  out.push(field.trim())
  return out
}

const HEADER: Record<string, keyof ContactInput> = {
  first_name: 'first_name', firstname: 'first_name', 'first name': 'first_name', name: 'first_name',
  email: 'email', 'email address': 'email', e_mail: 'email',
  phone: 'phone', 'phone number': 'phone', whatsapp: 'phone', mobile: 'phone',
  email_consent: 'email_consent', 'email consent': 'email_consent',
  whatsapp_consent: 'whatsapp_consent', 'whatsapp consent': 'whatsapp_consent',
}

/**
 * Reads a contacts CSV. Consent comes from the file's consent columns when it has them; otherwise only from
 * `consentForAll` (the operator confirming everyone in the file agreed). It is never assumed.
 */
export function parseContactsCsv(text: string, consentForAll: boolean): ParsedContacts {
  const lines = text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(line => line.trim())
  if (!lines.length) return { rows: [], skipped: 0, consentColumns: false }
  const header = splitCsvLine(lines[0]).map(cell => HEADER[cell.toLowerCase()])
  if (!header.includes('email') && !header.includes('phone')) throw new Error('The first row must name the columns, with at least an email or phone column.')
  const consentColumns = header.includes('email_consent') || header.includes('whatsapp_consent')
  const rows: ContactInput[] = []
  let skipped = 0
  for (const line of lines.slice(1)) {
    const cells = splitCsvLine(line)
    const value = (key: keyof ContactInput) => { const i = header.indexOf(key); return i >= 0 ? (cells[i] ?? '').trim() : '' }
    const email = value('email').toLowerCase() || null
    const phone = value('phone') || null
    if (!email && !phone) { skipped++; continue }
    const consent = (key: 'email_consent' | 'whatsapp_consent') => header.includes(key) ? YES.has(value(key).toLowerCase()) : consentForAll
    rows.push({ first_name: value('first_name').split(/\s+/)[0] ?? '', email, phone, email_consent: Boolean(email) && consent('email_consent'), whatsapp_consent: Boolean(phone) && consent('whatsapp_consent') })
  }
  return { rows, skipped, consentColumns }
}
