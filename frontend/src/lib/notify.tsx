import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'

export interface Notice {
  text: string
  link?: { to: string; label: string }
}

const NotifyContext = createContext<(notice: Notice | string) => void>(() => {})

export function useNotify() {
  return useContext(NotifyContext)
}

/** One polite live region for confirmations. Errors are shown next to the action as role=alert. */
export function NotifyProvider({ children }: { children: ReactNode }) {
  const [notice, setNotice] = useState<Notice | null>(null)
  const [seq, setSeq] = useState(0)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const notify = useCallback((n: Notice | string) => {
    setNotice(typeof n === 'string' ? { text: n } : n)
    setSeq((s) => s + 1)
  }, [])

  useEffect(() => {
    if (!notice) return
    clearTimeout(timer.current)
    timer.current = setTimeout(() => setNotice(null), notice.link ? 12000 : 6000)
    return () => clearTimeout(timer.current)
  }, [notice, seq])

  return (
    <NotifyContext.Provider value={notify}>
      {children}
      <div className="notice-region" role="status" aria-live="polite">
        {notice && (
          <p className="notice" key={seq}>
            <span>{notice.text}</span>
            {notice.link && (
              <Link to={notice.link.to} className="notice-link">
                {notice.link.label}
              </Link>
            )}
            <button type="button" className="notice-close" aria-label="Dismiss message" onClick={() => setNotice(null)}>
              ×
            </button>
          </p>
        )}
      </div>
    </NotifyContext.Provider>
  )
}
