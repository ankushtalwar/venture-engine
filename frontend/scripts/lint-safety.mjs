// Cheap lint: the same safety rules as src/test/safety.test.ts, runnable without Vitest.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const root = join(import.meta.dirname, '..', 'src')
const rules = [
  [/dangerouslySetInnerHTML|\.innerHTML\s*=|insertAdjacentHTML|document\.write/, 'injects HTML'],
  [/\b(solutions?|product ideas?|business ideas?|startup ideas?|opportunit(y|ies))\b/i, 'solution/idea copy'],
  [/author_hash|author_key/, 'reads author hashes'],
]

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === 'test' ? [] : walk(path)
    return /\.(tsx?|css)$/.test(name) ? [path] : []
  })
}

let failures = 0
for (const path of walk(root)) {
  const text = readFileSync(path, 'utf8')
  for (const [re, what] of rules) {
    if (re.test(text)) {
      console.error(`${relative(root, path)}: ${what}`)
      failures++
    }
  }
  for (const m of text.matchAll(/<a\b[^>]*target="_blank"[^>]*>/g)) {
    if (!m[0].includes('rel="noopener noreferrer"')) {
      console.error(`${relative(root, path)}: target=_blank without rel="noopener noreferrer"`)
      failures++
    }
  }
}
if (failures) process.exit(1)
console.log('lint-safety: ok')
