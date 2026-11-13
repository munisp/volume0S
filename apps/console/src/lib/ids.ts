/** Business ids: made from the name, permanent, unique. */

/** The first free id: `base`, else `base-2`, `base-3`… (an id is permanent, so it is never reused or shared). */
export function freeId(base: string, existing: Set<string>): string {
  if (!existing.has(base)) return base
  for (let n = 2; ; n++) {
    const candidate = `${base.slice(0, 63 - String(n).length - 1)}-${n}`
    if (!existing.has(candidate)) return candidate
  }
}

export const ID_PATTERN = /^[a-z0-9][a-z0-9-]{1,62}$/

/** "Aster Foods Ltd" → "aster-foods-ltd": the business id and short name a new client gets unless changed. */
export function slugify(name: string): string {
  // NFKD splits "ọ" into "o" + its accent marks; drop the marks so Yoruba/Igbo/French names keep whole words.
  return name.normalize('NFKD').replace(/\p{M}+/gu, '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 63)
}
