import test from 'node:test'
import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import { operationCatalog } from '../dist/operations.js'

// The skill names MCP tools as `bench_<operation id>`; a renamed or removed
// operation must not leave the skill pointing at a tool that does not exist.
const root = 'skills/bench-sdk'

async function skillFiles(dir) {
  const files = []
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = `${dir}/${entry.name}`
    if (entry.isDirectory()) files.push(...(await skillFiles(path)))
    else if (path.endsWith('.md')) files.push(path)
  }
  return files
}

test('every bench_<operation> tool named in the skill exists in the catalog', async () => {
  const ids = new Set(operationCatalog.operations.map((o) => o.id))
  const missing = []
  for (const file of await skillFiles(root)) {
    const text = await readFile(file, 'utf8')
    for (const match of text.matchAll(/`bench_([a-z0-9_]+)`/g)) {
      if (!ids.has(match[1])) missing.push(`${file}: bench_${match[1]}`)
    }
  }
  assert.deepEqual(missing, [])
})

test('the skill still ships all five reference files', async () => {
  const names = (await readdir(`${root}/references`)).sort()
  assert.deepEqual(names, [
    'after-registering.md',
    'configuration-and-verification.md',
    'finding-calls.md',
    'fragments-and-conditions.md',
    'keys-and-shapes.md',
  ])
})
