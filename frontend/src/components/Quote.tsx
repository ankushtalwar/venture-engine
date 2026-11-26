import { splitRefLabel } from '../lib/format'
import { SourceLink } from './SourceLink'

/** A verbatim span from a source document. Serif type is reserved for source text. */
export function Quote({ quote, url, label }: { quote: string; url: string; label: string }) {
  const { source, when } = splitRefLabel(label)
  return (
    <figure className="quote" data-testid="quote">
      <blockquote>{quote}</blockquote>
      <figcaption>
        <SourceLink url={url} source={source} when={when && when !== 'undated' ? when : null} />
      </figcaption>
    </figure>
  )
}
