import { useEffect } from 'react'

export function useDocumentTitle(title: string) {
  useEffect(() => {
    document.title = title ? `${title} – Pain review` : 'Pain review'
  }, [title])
}
