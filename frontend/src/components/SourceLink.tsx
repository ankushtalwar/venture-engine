import type { ReactNode } from 'react'
import { date } from '../lib/format'

/** Only http(s) links are rendered as links; anything else from a source is shown as plain text. */
export function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null
  } catch {
    return null
  }
}

export function ExternalLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  const url = safeUrl(href)
  if (!url) return <span className={className}>{children}</span>
  return (
    <a href={url} target="_blank" rel="noopener noreferrer" className={className}>
      {children}
    </a>
  )
}

/** Source name (linked) and publication date. `when` may already be a formatted date or ISO string. */
export function SourceLink({ url, source, when }: { url: string; source: string; when?: string | null }) {
  return (
    <span className="source-ref">
      <ExternalLink href={url} className="source-name">
        {source}
      </ExternalLink>
      <time className="source-date" dateTime={when ?? undefined}>
        {when ? date(when) : 'undated'}
      </time>
    </span>
  )
}
