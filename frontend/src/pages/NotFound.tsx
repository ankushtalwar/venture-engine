import { Link } from 'react-router'
import { useDocumentTitle } from '../lib/title'

export function NotFound() {
  useDocumentTitle('Page not found')
  return (
    <section className="page">
      <h1>Page not found</h1>
      <p>
        There is nothing at this address. <Link to="/cards">Go to the pain cards</Link>.
      </p>
    </section>
  )
}
