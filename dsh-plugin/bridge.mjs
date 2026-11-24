import { execFile } from 'node:child_process'

const MAX_INPUT = 16 * 1024 * 1024
const MAX_ARGUMENTS = 64 * 1024

/** Execute the installed Python package with document bytes on stdin. */
export function createCompressor(python, store) {
  return async (args, text, signal) => {
    signal?.throwIfAborted()
    if (Buffer.byteLength(text) > MAX_INPUT) throw new Error('Compression input exceeds 16 MiB')
    if (args.reduce((size, arg) => size + Buffer.byteLength(arg) + 1, 0) > MAX_ARGUMENTS) {
      throw new Error('Compression arguments exceed 64 KiB')
    }
    // The compressor needs neither model credentials nor the DSH environment.
    const env = Object.fromEntries(['PATH', 'HOME', 'TMPDIR', 'TEMP', 'TMP', 'SYSTEMROOT', 'SystemRoot']
      .filter(key => process.env[key] !== undefined).map(key => [key, process.env[key]]))
    return new Promise((resolve, reject) => {
      const child = execFile(python, ['-I', '-B', '-X', 'utf8', '-m', 'context_compression',
        ...args, '--store', store], {
        encoding: 'utf8', env, signal, timeout: 120_000, killSignal: 'SIGKILL', maxBuffer: MAX_INPUT,
      }, (error, stdout, stderr) => {
        if (error) {
          if (signal?.aborted) return reject(signal.reason)
          // execFile's default error message contains argv and potentially the question.
          const reason = error.killed ? 'timeout or output limit' : String(error.code ?? 'launch failure')
          return reject(new Error('Python compression failed (' + reason + '). Check the configured interpreter and package.'))
        }
        try {
          const receipt = JSON.parse(stderr)
          if (!/^[a-f0-9]{64}$/.test(receipt.source_id) || !Number.isSafeInteger(receipt.input_tokens)) {
            throw new Error()
          }
          resolve({ text: stdout, receipt })
        } catch {
          reject(new Error('Invalid receipt from the Python compressor'))
        }
      })
      child.stdin.on('error', error => {
        if (error.code !== 'EPIPE') reject(new Error('Could not send input to the Python compressor'))
      })
      child.stdin.end(text, 'utf8')
    })
  }
}
