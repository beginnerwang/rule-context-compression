// Keep output policy on the rendered result. DSH tools and PTC retain their canonical values.
const QUERY_LIMIT = 2048

function questionFor(exec) {
  const messages = exec.agent?.session?.deriveMessages?.()
  const latest = messages?.findLast(message => message.role === 'user' && message.source?.kind === 'user')
  const parts = latest?.content ?? []
  let text = ''
  for (const part of parts) {
    if (part.type === 'text') {
      text += (text ? '\n' : '') + part.text.slice(0, QUERY_LIMIT - text.length)
      if (text.length >= QUERY_LIMIT) break
    }
  }
  if (!text.trim()) {
    const args = exec.arguments ?? {}
    text = [args.question, args.query, args.pattern, args.description]
      .find(value => typeof value === 'string' && value.trim()) ?? ''
  }
  // Keep argv bounded, including for long pasted prompts or a split UTF-16 surrogate.
  return text.slice(0, QUERY_LIMIT).replace(/[\u0000\uD800-\uDBFF]$/u, '').replaceAll('\u0000', ' ')
}

export function registerOutput(ctx, config, compress) {
  const names = new Set(config.tools)
  names.delete('compress_reading')
  if (!names.size || config.enabled === false) return

  ctx.on('tools/post-execute', async (exec, result, next) => {
    const decision = await next()
    if (decision.kind !== 'accept' || Object.hasOwn(decision, 'value') || Object.hasOwn(decision, 'content')
        || result.isError || exec.parent !== undefined || exec.signal.aborted || !names.has(exec.name)) {
      return decision
    }
    const [block] = result.content
    if (result.content.length !== 1 || block.type !== 'text' || !block.text) return decision

    try {
      const args = ['tool', '--budget', String(config.budget), '--question=' + questionFor(exec)]
      const { text, receipt } = await compress(args, block.text, exec.signal)
      if (exec.signal.aborted || !receipt.changed) return decision
      if (typeof text !== 'string' || !Number.isInteger(receipt.output_tokens)
          || receipt.output_tokens < 0 || receipt.output_tokens > config.budget
          || !Number.isInteger(receipt.input_tokens) || receipt.input_tokens <= receipt.output_tokens
          || typeof receipt.source_id !== 'string' || !/^[a-f0-9]{64}$/.test(receipt.source_id)) {
        throw new Error('Invalid compression receipt')
      }
      ctx.logger.info(`context-compression: ${exec.name} ${receipt.input_tokens} -> ${receipt.output_tokens} tokens; source_id=${receipt.source_id}`)
      return { ...decision, content: [{ ...block, text }] }
    } catch {
      if (!exec.signal.aborted) ctx.logger.warn(`context-compression: could not compress ${exec.name}; keeping its original output`)
      return decision
    }
  }, { prepend: true })
}
