import { homedir } from 'node:os'
import { isAbsolute, join } from 'node:path'
import { createCompressor } from './bridge.mjs'
import { registerReading } from './reading.mjs'
import { registerOutput } from './output.mjs'

export const name = 'rule-context-compression'
export const inject = ['tools']

function section(value, name) {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(name + ' must be a configuration object')
  }
  if (value.enabled !== undefined && typeof value.enabled !== 'boolean') {
    throw new TypeError(name + '.enabled must be a boolean')
  }
  return value
}

export function apply(ctx, config = {}) {
  const reading = section(config.reading === undefined ? {} : config.reading, 'reading')
  const output = section(config.output === undefined ? {} : config.output, 'output')
  if (reading.enabled === false && output.enabled === false) return
  const python = config.python ?? 'python3'
  const store = config.store ?? join(process.env.DSH_HOME || join(homedir(), '.dsh'), 'context-compression', 'originals')
  if (typeof python !== 'string' || !python.trim()) throw new TypeError('python must name a Python executable')
  if (typeof store !== 'string' || !isAbsolute(store)) throw new TypeError('store must be an absolute directory')
  const compress = createCompressor(python, store)
  if (reading.enabled !== false) registerReading(ctx, { ...reading, documents: reading.documents ?? {} }, compress)
  if (output.enabled !== false) {
    const tools = output.tools ?? ['grep', 'glob']
    const budget = output.budget ?? 1024
    if (!Array.isArray(tools) || !tools.every(tool => typeof tool === 'string' && tool.trim() === tool && tool.length)) {
      throw new TypeError('output.tools must contain exact tool names')
    }
    if (!Number.isSafeInteger(budget) || budget < 1) throw new TypeError('output.budget must be a positive integer')
    registerOutput(ctx, { tools, budget }, compress)
  }
}
