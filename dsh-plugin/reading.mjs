import { constants, lstatSync, realpathSync } from 'node:fs'
import { open } from 'node:fs/promises'
import { isAbsolute } from 'node:path'

const MAX_DOCUMENT_BYTES = 16 * 1024 * 1024

// A document ID grants access to one operator-selected file, never a directory.
// Pin its identity when the plugin loads, without reading its contents.
function bindDocuments(entries) {
  const documents = new Map()
  if (entries === null || typeof entries !== 'object' || Array.isArray(entries)) {
    throw new TypeError('reading.documents must map document IDs to absolute file paths')
  }
  for (const [id, path] of Object.entries(entries)) {
    if (!id.trim() || typeof path !== 'string' || !isAbsolute(path)) {
      throw new TypeError('reading.documents requires a nonempty ID and absolute path for each file')
    }
    const stat = lstatSync(path, { bigint: true })
    if (!stat.isFile()) throw new Error(`Configured document ${id} must be a regular file, not a symbolic link`)
    documents.set(id, { path: realpathSync(path), dev: stat.dev, ino: stat.ino })
  }
  return documents
}

async function readDocument(document, signal) {
  signal.throwIfAborted()
  let file
  try {
    // NOFOLLOW rejects a replaced leaf; NONBLOCK avoids hanging on a substituted FIFO.
    file = await open(document.path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK)
  } catch {
    throw new Error('Configured document is unavailable')
  }
  try {
    const before = await file.stat({ bigint: true })
    if (!before.isFile() || before.dev !== document.dev || before.ino !== document.ino) {
      throw new Error('Configured document has been replaced; reload the plugin to bind it again')
    }
    if (before.size > BigInt(MAX_DOCUMENT_BYTES)) throw new Error('Document exceeds the 16 MiB reading limit')
    const buffer = Buffer.alloc(Number(before.size))
    let offset = 0
    while (offset < buffer.length) {
      signal.throwIfAborted()
      const { bytesRead } = await file.read(buffer, offset, Math.min(65536, buffer.length - offset), offset)
      if (!bytesRead) throw new Error('Document changed during reading')
      offset += bytesRead
    }
    const after = await file.stat({ bigint: true })
    if (before.size !== after.size || before.mtimeNs !== after.mtimeNs || before.ctimeNs !== after.ctimeNs) {
      throw new Error('Document changed during reading')
    }
    signal.throwIfAborted()
    // Preserve a leading BOM as U+FEFF, just like Python's strict UTF-8 decoder.
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(buffer)
  } finally {
    await file.close()
  }
}

export function registerReading(ctx, config, compress) {
  if (config.enabled === false) return
  const documents = bindDocuments(config.documents ?? {})
  const ids = [...documents.keys()]
  return ctx.tools.register({
    name: 'compress_reading',
    description: ids.length
      ? 'Use for a reading subtask on a configured document. Supply the question and all offered options. Returns verbatim evidence with zero-based Unicode character offsets.'
      : 'No reading documents are configured. An operator can register document IDs in reading.documents.',
    parameters: {
      type: 'object', additionalProperties: false, required: ['document_id', 'question'],
      properties: {
        document_id: { type: 'string', description: 'A configured document ID.', ...(ids.length ? { enum: ids } : {}) },
        question: { type: 'string', description: 'The question to answer using the document.' },
        options: { type: 'object', additionalProperties: true, description: 'All multiple-choice labels and their string values, if applicable.' },
      },
    },
    output: {
      schema: { type: 'string' },
      render: (_args, value) => [{ type: 'text', text: value }],
    },
    async execute(args, exec) {
      const document = documents.get(args?.document_id)
      if (!document) throw new Error('Unknown reading document ID')
      if (typeof args.question !== 'string' || !args.question.trim()) throw new Error('A nonempty question is required')
      const options = args.options === undefined ? {} : args.options
      if (!options || typeof options !== 'object' || Array.isArray(options) ||
          !Object.values(options).every(value => typeof value === 'string')) {
        throw new Error('Reading options must map option labels to strings')
      }
      const raw = await readDocument(document, exec.signal)
      const result = await compress(['read', '--question=' + args.question, '--options=' + JSON.stringify(options)], raw, exec.signal)
      return result.text
    },
  })
}
