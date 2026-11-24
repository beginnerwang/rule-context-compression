// Run with DSH_CLI_ENTRY pointing to an installed DSH CLI JavaScript entry and
// RCC_TEST_PYTHON pointing to a Python environment containing this project.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const ownPath = fileURLToPath(import.meta.url)
const bundle = resolve(dirname(ownPath), '../dsh-plugin')
const documentText = '🚉北站每天八点开放。南站每天九点开放。'
const question = 'Which job failed and what is its error code?'
const jobs = JSON.stringify(Array.from({ length: 80 }, (_, id) => ({
  id, status: id === 23 ? 'failed' : 'ok', error: id === 23 ? 'E_TIMEOUT' : null,
})))

export const name = 'context-compression-test-driver'
export const inject = ['tools']

// This file also acts as the fixture plugin inside the isolated DSH process.
export async function apply(ctx) {
  const { defineTool } = await import(process.env.TEST_TOOLS_MODULE)
  const output = { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] }
  const tools = ['fixture_jobs', 'fixture_other', 'fixture_failure', 'fixture_blocks', 'fixture_policy']
  for (const toolName of tools) {
    ctx.tools.register(defineTool({
      name: toolName, description: 'Offline test fixture',
      parameters: { question: { type: 'string', required: true } },
      output: toolName === 'fixture_blocks' ? { ...output, render: (_args, value) => [
        { type: 'text', text: value }, { type: 'text', text: 'second block' },
      ] } : output,
      async execute() {
        if (toolName === 'fixture_failure') throw Error('fixture failure sentinel')
        return jobs
      },
    }))
  }

  ctx.on('tools/post-execute', async (exec, result, next) => {
    if (exec.name === 'fixture_policy') return { kind: 'accept', content: [{ type: 'text', text: 'already transformed' }] }
    return next()
  })

  void ctx.loader.await().then(async () => {
    const { reading, output: outputEnabled, broken } = JSON.parse(process.env.TEST_SCENARIO)
    const evidenceTool = 'compress_reading'
    const available = ctx.tools.schemas().map(tool => tool.name)
    assert.equal(available.includes(evidenceTool), reading)
    let calls = 0
    const run = (name, args) => ctx.tools.execute({
      name, arguments: args, callId: 'fixture-' + (++calls), signal: new AbortController().signal,
    })
    const expectText = (result, expected) => {
      assert.ok(!result.isError, JSON.stringify(result.content))
      assert.deepEqual(result.content, [{ type: 'text', text: expected }])
      assert.equal(result.value, expected)
    }

    const before = await run('fixture_other', { question })
    expectText(before, jobs)
    assert.ok(!existsSync(process.env.TEST_STORE), 'a non-selected tool invoked compression')
    const bad = await run('fixture_failure', { question })
    assert.ok(bad.isError)
    assert.match(JSON.stringify(bad.content), /fixture failure sentinel/)
    assert.ok(!existsSync(process.env.TEST_STORE), 'a failed tool invoked compression')

    const blocks = await run('fixture_blocks', { question })
    assert.deepEqual(blocks.content.map(block => block.text), [jobs, 'second block'])
    const policy = await run('fixture_policy', { question })
    assert.equal(policy.value, jobs)
    assert.deepEqual(policy.content, [{ type: 'text', text: 'already transformed' }])
    assert.ok(!existsSync(process.env.TEST_STORE), 'a skipped or already-transformed result invoked Python')

    const processed = await run('fixture_jobs', { question })
    if (outputEnabled && !broken) {
      assert.ok(!processed.isError)
      assert.equal(processed.value, jobs, 'programmatic callers must retain the canonical value')
      assert.equal(processed.content.length, 1)
      const visible = processed.content[0].text
      assert.notEqual(visible, jobs)
      assert.match(visible, /E_TIMEOUT/)
      const counted = spawnSync(process.env.TEST_GOOD_PYTHON, ['-I', '-B', '-c',
        'import sys; from context_compression.tokens import TokenCounter; print(TokenCounter().text(sys.stdin.read()))'],
      { input: visible, encoding: 'utf8' })
      assert.equal(counted.status, 0, counted.stderr)
      assert.ok(Number(counted.stdout) <= 128, 'model-visible text exceeds budget')
    } else {
      expectText(processed, jobs)
    }

    if (reading) {
      const args = { question: '南站几点开放？', document_id: 'station' }
      const read = await run(evidenceTool, args)
      if (broken) {
        assert.ok(read.isError, 'reading must explicitly report a missing compressor')
      } else {
        assert.ok(!read.isError, JSON.stringify(read.content))
        assert.equal(read.value, read.content[0].text)
        const { passages } = JSON.parse(read.value)
        assert.ok(passages.some(row => row.text.includes('南站每天九点开放')))
        for (const row of passages) assert.equal(row.text, Array.from(documentText).slice(row.start, row.end).join(''))
        const escaped = await run(evidenceTool, { ...args, document_id: '../private.txt' })
        assert.ok(escaped.isError)
        assert.ok(!JSON.stringify(escaped.content).includes('PRIVATE_SENTINEL'))
      }
    } else {
      const missing = await run(evidenceTool, { document_id: 'station', question: '南站？' })
      assert.ok(missing.isError)
    }
    writeFileSync(process.env.TEST_DONE, 'passed')
  }).catch(error => {
    console.error(error)
    process.exitCode = 1
  })
}

function runSuite() {
  const entry = process.env.DSH_CLI_ENTRY
  assert.ok(entry && existsSync(entry), 'Set DSH_CLI_ENTRY to the installed dsh CLI JavaScript entry.')
  const cli = realpathSync(entry)
  const cliRequire = createRequire(cli)
  const dsh = createRequire(cliRequire.resolve('@deepseek-ai/dsh-base/package.json'))
  const modules = ['@deepseek-ai/dsh-system-prompt', '@deepseek-ai/dsh-tools']
    .map(pkg => pathToFileURL(dsh.resolve(pkg)).href)
  const python = process.env.RCC_TEST_PYTHON || 'python3'
  const manifest = JSON.parse(readFileSync(join(bundle, 'package.json'), 'utf8'))
  const root = mkdtempSync(join(tmpdir(), 'context-compression-dsh-'))
  const scenarios = [
    { reading: true, output: true },
    { reading: true, output: false },
    { reading: false, output: true },
    { reading: false, output: false },
    { reading: true, output: true, broken: true },
  ]
  try {
    writeFileSync(join(root, 'station.txt'), documentText)
    writeFileSync(join(root, 'private.txt'), 'PRIVATE_SENTINEL')
    for (const [index, scenario] of scenarios.entries()) {
      const home = join(root, String(index))
      const profile = join(home, 'profiles', 'integration')
      const installed = join(profile, 'node_modules', manifest.name)
      mkdirSync(dirname(installed), { recursive: true })
      cpSync(bundle, installed, { recursive: true, filter: path => !path.endsWith('/node_modules') })
      writeFileSync(join(profile, 'package.json'), JSON.stringify({
        name: 'dsh-test-profile', private: true, dependencies: { [manifest.name]: manifest.version },
        dsh: { profile: { bundles: [manifest.name] } },
      }))
      const store = join(home, 'originals')
      const done = join(home, 'done')
      const patch = [
        { id: 'rule-context-compression', config: {
          python: scenario.broken ? join(home, 'missing-python') : python, store,
          reading: { enabled: scenario.reading, documents: { station: join(root, 'station.txt') } },
          output: { enabled: scenario.output, budget: 128,
            tools: ['fixture_jobs', 'fixture_failure', 'fixture_blocks', 'fixture_policy', 'compress_reading'] },
        } },
        { insert: [
          { id: 'fixture-system-prompt', name: modules[0] },
          { id: 'fixture-tools', name: modules[1] },
          { id: 'fixture-driver', name: import.meta.url },
        ] },
      ]
      writeFileSync(join(profile, 'cordis.patch.yml'), JSON.stringify(patch))
      // Do not inherit production settings, tokens, sessions, uploads or model providers.
      const env = {
        HOME: home, DSH_HOME: home, PATH: process.env.PATH, LANG: 'en_US.UTF-8',
        TEST_GOOD_PYTHON: python, TEST_SCENARIO: JSON.stringify(scenario),
        TEST_TOOLS_MODULE: modules[1], TEST_STORE: store, TEST_DONE: done,
      }
      const r = spawnSync(process.execPath, [cli, '--profile', 'integration'], {
        cwd: home, env, encoding: 'utf8', timeout: 60000, stdio: ['ignore', 'pipe', 'pipe'],
      })
      assert.equal(r.status, 0, r.error?.message || r.stderr || r.stdout)
      assert.ok(existsSync(done), 'fixture did not complete: ' + r.stderr)
      console.log('passed', JSON.stringify(scenario))
    }
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(ownPath)) runSuite()
