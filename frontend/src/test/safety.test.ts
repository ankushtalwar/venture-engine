import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..')

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'test' ? [] : files(path)
    return /\.(tsx?|css)$/.test(name) ? [path] : []
  })
}

const sources = files(SRC).map((path) => ({ path: relative(SRC, path), text: readFileSync(path, 'utf8') }))

describe('source safety rules', () => {
  it('never injects HTML', () => {
    for (const { path, text } of sources) {
      expect(text, path).not.toMatch(/dangerouslySetInnerHTML|\.innerHTML\s*=|insertAdjacentHTML|document\.write/)
    }
  })

  it('gives every new-tab link rel="noopener noreferrer"', () => {
    for (const { path, text } of sources) {
      for (const m of text.matchAll(/<a\b[^>]*target="_blank"[^>]*>/g)) {
        expect(m[0], path).toContain('rel="noopener noreferrer"')
      }
    }
  })

  it('has no solution, product or idea copy', () => {
    for (const { path, text } of sources) {
      expect(text, path).not.toMatch(/\b(solutions?|product ideas?|business ideas?|startup ideas?|opportunit(y|ies))\b/i)
    }
  })

  it('never reads author hashes', () => {
    for (const { path, text } of sources) {
      expect(text, path).not.toMatch(/author_hash|author_key/)
    }
  })
})
