import { Component, type ErrorInfo, type ReactNode } from 'react'

/** Keeps the shell and navigation alive if one view hits data it can't render. */
export class ErrorBoundary extends Component<{ resetKey: string; children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: Error) {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('View failed to render', error, info.componentStack)
  }

  componentDidUpdate(prev: { resetKey: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null })
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <section className="page">
        <h1>This view could not be shown</h1>
        <div className="error" role="alert">
          <p>{this.state.error.message || 'The data from the API had an unexpected shape.'}</p>
        </div>
        <p>Use the navigation to open another view, or reload the page.</p>
      </section>
    )
  }
}
