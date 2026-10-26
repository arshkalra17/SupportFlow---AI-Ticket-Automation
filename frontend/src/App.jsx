import { useState, useCallback, useEffect, useRef } from 'react'

const API = 'http://localhost:8001'

const TERMINAL_TICKET_STATUSES = new Set(['PROCESSED', 'CLASSIFICATION_FAILED', 'QUEUE_FAILED'])

// ── Icons ──────────────────────────────────────────────────────────────────
const Icon = {
  Support: () => (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
    </svg>
  ),
  Ticket: () => (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 6v.75m0 3v.75m0 3v.75m0 3V18m-9-5.25h5.25M7.5 15h3M3.375 5.25c-.621 0-1.125.504-1.125 1.125v3.026a2.999 2.999 0 0 1 0 5.198v3.026c0 .621.504 1.125 1.125 1.125h17.25c.621 0 1.125-.504 1.125-1.125v-3.026a2.999 2.999 0 0 1 0-5.198V6.375c0-.621-.504-1.125-1.125-1.125H3.375Z" />
    </svg>
  ),
  Approvals: () => (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" />
    </svg>
  ),
  Send: () => (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 12 3.269 3.125A59.769 59.769 0 0 1 21.485 12 59.768 59.768 0 0 1 3.27 20.875L5.999 12Zm0 0h7.5" />
    </svg>
  ),
  Logout: () => (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15m3 0 3-3m0 0-3-3m3 3H9" />
    </svg>
  ),
  Bot: () => (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 0 1-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 0 1 4.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0 1 12 15a9.065 9.065 0 0 1-6.23-.693L4.2 13.914m15.6 1.386-.676 6.957a2.25 2.25 0 0 1-2.241 2.018H7.117a2.25 2.25 0 0 1-2.241-2.018L4.2 15.3M19.8 15.3l.014-.014" />
    </svg>
  ),
  Check: () => (
    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  ),
  X: () => (
    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2.5} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
    </svg>
  ),
  Doc: () => (
    <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z" />
    </svg>
  ),
  Refresh: () => (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
    </svg>
  ),
  Plus: () => (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
    </svg>
  ),
}

// ── Small reusable components ──────────────────────────────────────────────
const Badge = ({ children, color = 'gray', size = 'sm' }) => {
  const colors = {
    gray:   'bg-gray-100 text-gray-600',
    blue:   'bg-blue-50 text-blue-700',
    green:  'bg-emerald-50 text-emerald-700',
    yellow: 'bg-amber-50 text-amber-700',
    red:    'bg-red-50 text-red-600',
    orange: 'bg-orange-50 text-orange-700',
    purple: 'bg-purple-50 text-purple-700',
  }
  const sizes = { xs: 'px-1.5 py-0.5 text-xs', sm: 'px-2 py-0.5 text-xs' }
  return (
    <span className={`inline-flex items-center rounded-full font-medium ${colors[color]} ${sizes[size]}`}>
      {children}
    </span>
  )
}

const Spinner = ({ size = 'md', className = '' }) => {
  const sizes = { sm: 'h-4 w-4 border-2', md: 'h-6 w-6 border-2', lg: 'h-8 w-8 border-[3px]' }
  return (
    <div className={`animate-spin rounded-full border-gray-200 border-t-indigo-500 ${sizes[size]} ${className}`} />
  )
}

const ThinkingDots = () => (
  <div className="flex items-center gap-1 py-1">
    {[0, 1, 2].map(i => (
      <span
        key={i}
        className="dot-bounce inline-block h-1.5 w-1.5 rounded-full bg-gray-400"
        style={{ animationDelay: `${i * 0.2}s` }}
      />
    ))}
  </div>
)

const Alert = ({ type = 'error', children }) => {
  const styles = {
    error:   'bg-red-50 border-red-200 text-red-700',
    warning: 'bg-amber-50 border-amber-200 text-amber-700',
    success: 'bg-emerald-50 border-emerald-200 text-emerald-700',
  }
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm fade-in ${styles[type]}`}>{children}</div>
  )
}

// ── Strip markdown formatting from LLM responses ──────────────────────────
const stripMarkdown = (text) => {
  if (!text) return text
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')   // **bold**
    .replace(/\*(.+?)\*/g, '$1')        // *italic*
    .replace(/__(.+?)__/g, '$1')        // __bold__
    .replace(/_(.+?)_/g, '$1')          // _italic_
    .replace(/`(.+?)`/g, '$1')          // `code`
    .replace(/~~(.+?)~~/g, '$1')        // ~~strikethrough~~
}

// ── Priority / status mappings ─────────────────────────────────────────────
const priorityColor = p => ({ URGENT: 'red', HIGH: 'orange', MEDIUM: 'yellow', LOW: 'gray', urgent: 'red', high: 'orange', medium: 'yellow', low: 'gray' }[p] || 'gray')
const sentimentColor = s => ({ positive: 'green', POSITIVE: 'green', negative: 'red', NEGATIVE: 'red', neutral: 'gray', NEUTRAL: 'gray' }[s] || 'gray')
const ticketStatusColor = s => ({ PENDING: 'yellow', PROCESSED: 'green', CLASSIFICATION_FAILED: 'red', QUEUE_FAILED: 'red' }[s] || 'gray')
const approvalStatusColor = s => ({ PENDING_APPROVAL: 'orange', APPROVED: 'green', REJECTED: 'red' }[s] || 'gray')

// ── Login screen ───────────────────────────────────────────────────────────
const LoginScreen = ({ onLogin }) => {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const res = await fetch(`${API}/auth/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || 'Invalid credentials')
      }
      const data = await res.json()
      onLogin(data.access_token)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex" style={{ background: 'linear-gradient(135deg, #1e1b4b 0%, #312e81 40%, #4338ca 100%)' }}>
      {/* Left panel */}
      <div className="hidden lg:flex flex-col justify-between w-1/2 p-12 text-white">
        <div className="flex items-center gap-2.5">
          <div className="h-8 w-8 rounded-lg bg-white/20 flex items-center justify-center backdrop-blur">
            <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
            </svg>
          </div>
          <span className="font-bold text-lg tracking-tight">SupportFlow</span>
        </div>

        <div>
          <p className="text-4xl font-bold leading-tight mb-4">
            AI-powered<br />customer support,<br />built different.
          </p>
          <p className="text-indigo-200 text-lg leading-relaxed max-w-sm">
            LangGraph agentic pipeline with real-time tool execution, semantic routing, and human-in-the-loop approvals.
          </p>

          <div className="mt-10 space-y-4">
            {[
              ['328', 'deterministic tests passing'],
              ['10',  'backend tools wired to production data'],
              ['RAG', 'knowledge base with semantic retrieval'],
            ].map(([stat, label]) => (
              <div key={stat} className="flex items-center gap-3">
                <div className="h-8 w-8 rounded-md bg-white/10 flex items-center justify-center text-xs font-bold shrink-0">
                  {stat}
                </div>
                <span className="text-indigo-200 text-sm">{label}</span>
              </div>
            ))}
          </div>
        </div>

        <p className="text-indigo-300 text-xs">Built with FastAPI · LangGraph · PostgreSQL · Redis · Groq</p>
      </div>

      {/* Right panel */}
      <div className="flex-1 flex items-center justify-center p-6 bg-white lg:rounded-l-3xl">
        <div className="w-full max-w-sm">
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center justify-center gap-2 mb-8">
            <div className="h-8 w-8 rounded-lg bg-indigo-600 flex items-center justify-center">
              <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
              </svg>
            </div>
            <span className="font-bold text-xl text-gray-900">SupportFlow</span>
          </div>

          <h2 className="text-2xl font-bold text-gray-900 mb-1">Welcome back</h2>
          <p className="text-sm text-gray-500 mb-8">Sign in to your support account</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="login-email" className="block text-sm font-medium text-gray-700 mb-1.5">
                Email address
              </label>
              <input
                id="login-email" type="email" value={email}
                onChange={e => setEmail(e.target.value)} required autoFocus
                className="w-full px-3.5 py-2.5 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition"
                placeholder="you@company.com"
              />
            </div>
            <div>
              <label htmlFor="login-password" className="block text-sm font-medium text-gray-700 mb-1.5">
                Password
              </label>
              <input
                id="login-password" type="password" value={password}
                onChange={e => setPassword(e.target.value)} required
                className="w-full px-3.5 py-2.5 rounded-lg border border-gray-300 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition"
                placeholder="••••••••"
              />
            </div>

            {error && <Alert type="error">{error}</Alert>}

            <button
              type="submit" disabled={loading}
              className="w-full flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-400 text-white py-2.5 rounded-lg text-sm font-semibold transition-colors"
            >
              {loading ? <><Spinner size="sm" /><span>Signing in…</span></> : 'Sign in'}
            </button>
          </form>

          <p className="mt-6 text-center text-xs text-gray-400">
            SupportFlow &mdash; AI Customer Support Demo
          </p>
        </div>
      </div>
    </div>
  )
}

// ── Chat / Support view ────────────────────────────────────────────────────
const SupportView = ({ token, onLogout }) => {
  const [message, setMessage] = useState('')
  const [sentMessage, setSentMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [response, setResponse] = useState(null)
  const [error, setError] = useState('')
  const textareaRef = useRef(null)

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }, [message])

  const handleSend = async () => {
    if (!message.trim()) return
    setError('')
    setSending(true)
    setResponse(null)
    const trimmed = message.trim()
    setSentMessage(trimmed)
    try {
      const res = await fetch(`${API}/support/process`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ message: trimmed }),
      })
      if (res.status === 401) { onLogout(); return }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `Request failed (${res.status})`)
      }
      const data = await res.json()
      setResponse(data)
      setMessage('')
    } catch (err) {
      setError(err.message)
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSend()
  }

  const cls = response?.classification

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="mb-6">
        <h2 className="text-xl font-bold text-gray-900">Support Chat</h2>
        <p className="text-sm text-gray-500 mt-0.5">
          Ask a question or request an action — the AI agent handles everything in real time.
        </p>
      </div>

      {/* Conversation area */}
      <div className="flex-1 flex flex-col gap-4 mb-4">

        {/* Empty state */}
        {!response && !sending && (
          <div className="flex-1 flex flex-col items-center justify-center text-center py-16 fade-in">
            <div className="h-14 w-14 rounded-2xl bg-indigo-50 flex items-center justify-center mb-4">
              <svg className="w-7 h-7 text-indigo-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M8.625 12a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H8.25m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0H12m4.125 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 0 1-2.555-.337A5.972 5.972 0 0 1 5.41 20.97a5.969 5.969 0 0 1-.474-.065 4.48 4.48 0 0 0 .978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25Z" />
              </svg>
            </div>
            <p className="font-semibold text-gray-700 mb-1">Ask me anything</p>
            <p className="text-sm text-gray-400 max-w-xs">
              Try "Check my last order", "Request a refund for order 1011", or "What is your return policy?"
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-2">
              {[
                'Show me my orders',
                "What's my order status?",
                'Request a refund',
                'Cancel my order',
              ].map(q => (
                <button
                  key={q}
                  onClick={() => setMessage(q)}
                  className="px-3 py-1.5 text-xs font-medium rounded-full border border-gray-200 text-gray-600 hover:border-indigo-300 hover:text-indigo-600 hover:bg-indigo-50 transition-colors"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Thinking indicator */}
        {sending && (
          <div className="fade-in">
            {/* User bubble */}
            <div className="flex justify-end mb-3">
              <div className="bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-5 py-3 max-w-sm text-sm leading-relaxed shadow-sm">
                {sentMessage}
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="h-8 w-8 rounded-full bg-indigo-100 flex items-center justify-center shrink-0 mt-0.5">
                <Icon.Bot />
              </div>
              <div className="bg-white rounded-2xl rounded-tl-sm border border-gray-100 shadow-xs px-4 py-3">
                <ThinkingDots />
              </div>
            </div>
          </div>
        )}

        {/* Response bubble */}
        {response && !sending && (
          <div className="space-y-4 fade-in">
            {/* User bubble */}
            <div className="flex justify-end">
              <div className="bg-indigo-600 text-white rounded-2xl rounded-tr-sm px-5 py-3 max-w-sm text-sm leading-relaxed shadow-sm">
                {sentMessage}
              </div>
            </div>
            {/* AI reply bubble */}
            <div className="flex items-start gap-3">
              <div className="h-8 w-8 rounded-full bg-indigo-600 flex items-center justify-center shrink-0 mt-0.5">
                <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={1.8} stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
                </svg>
              </div>
              <div className="flex-1">
                <div className="bg-white rounded-2xl rounded-tl-sm border border-gray-100 shadow-sm px-5 py-4">
                  <p className="text-gray-800 text-sm leading-relaxed">{stripMarkdown(response.final_response)}</p>
                </div>

                {/* Metadata row */}
                <div className="mt-2 flex flex-wrap items-center gap-1.5 ml-1">
                  {cls?.category && (
                    <Badge color="blue">{cls.category}</Badge>
                  )}
                  {cls?.priority && (
                    <Badge color={priorityColor(cls.priority)}>{cls.priority} Priority</Badge>
                  )}
                  {cls?.sentiment && (
                    <Badge color={sentimentColor(cls.sentiment)}>{cls.sentiment}</Badge>
                  )}
                  {response.approval_status && (
                    <Badge color={approvalStatusColor(response.approval_status)}>
                      {response.approval_status.replace(/_/g, ' ')}
                    </Badge>
                  )}
                </div>
              </div>
            </div>

            {/* Tool execution cards */}
            {response.tool_calls && response.tool_calls.length > 0 && (
              <div className="ml-11">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Actions executed</p>
                <div className="space-y-2">
                  {response.tool_calls.map((tool, idx) => (
                    <div key={idx} className="bg-white border border-gray-100 rounded-xl shadow-xs p-4">
                      <div className="flex items-center justify-between mb-3">
                        <span className="font-mono text-xs font-semibold text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded">
                          {tool.tool_name}
                        </span>
                        <div className="flex items-center gap-3">
                          {[
                            [tool.validation_passed,  'Validated'],
                            [tool.authorization_passed,'Authorized'],
                            [tool.backend_executed,    'Executed'],
                          ].map(([ok, label]) => (
                            <span key={label} className="flex items-center gap-1">
                              <span className={`flex items-center justify-center h-4 w-4 rounded-full ${ok ? 'bg-emerald-100 text-emerald-600' : 'bg-red-100 text-red-500'}`}>
                                {ok ? <Icon.Check /> : <Icon.X />}
                              </span>
                              <span className="text-xs text-gray-500">{label}</span>
                            </span>
                          ))}
                        </div>
                      </div>
                      {tool.tool_args && Object.keys(tool.tool_args).length > 0 && (
                        <div className="grid grid-cols-2 gap-x-6 gap-y-1">
                          {Object.entries(tool.tool_args).map(([k, v]) => (
                            <div key={k} className="flex items-baseline gap-1.5 text-xs">
                              <span className="text-gray-400 font-medium shrink-0">{k}</span>
                              <span className="text-gray-700 truncate">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* RAG documents */}
            {response.retrieved_documents && response.retrieved_documents.length > 0 && (
              <div className="ml-11">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">Knowledge base sources</p>
                <div className="space-y-1">
                  {response.retrieved_documents.map((doc, idx) => (
                    <div key={idx} className="flex items-center gap-2 text-xs text-gray-600 bg-white border border-gray-100 rounded-lg px-3 py-2">
                      <Icon.Doc />
                      <span>{doc.title || doc}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Input area */}
      <div className="bg-white border border-gray-200 rounded-2xl shadow-sm p-3">
        {error && (
          <div className="mb-2">
            <Alert type="error">{error}</Alert>
          </div>
        )}
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={message}
            onChange={e => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            className="flex-1 resize-none text-sm text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent py-1 px-1 max-h-40"
            placeholder="Type a message… (⌘↵ to send)"
            disabled={sending}
            style={{ lineHeight: '1.5' }}
          />
          <button
            onClick={handleSend}
            disabled={sending || !message.trim()}
            className="h-9 w-9 flex items-center justify-center rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-200 text-white disabled:text-gray-400 transition-colors shrink-0"
          >
            {sending ? <Spinner size="sm" /> : <Icon.Send />}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Tickets view ───────────────────────────────────────────────────────────
const TicketsView = ({ token, onLogout }) => {
  const [ticketMessage, setTicketMessage] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [ticketResult, setTicketResult] = useState(null)
  const [ticketStatus, setTicketStatus] = useState(null)
  const [error, setError] = useState('')
  const pollingRef = useRef(null)

  const stopPolling = useCallback(() => {
    if (pollingRef.current) { clearInterval(pollingRef.current); pollingRef.current = null }
  }, [])
  useEffect(() => () => stopPolling(), [stopPolling])

  const pollTicket = useCallback(async (ticketId) => {
    try {
      const res = await fetch(`${API}/tickets/${ticketId}`, { headers: { Authorization: `Bearer ${token}` } })
      if (res.status === 401) { onLogout(); return }
      if (!res.ok) return
      const data = await res.json()
      setTicketStatus(data)
      if (TERMINAL_TICKET_STATUSES.has(data.status)) stopPolling()
    } catch { /* silent */ }
  }, [token, stopPolling, onLogout])

  const handleSubmit = async () => {
    if (!ticketMessage.trim()) return
    setError('')
    setSubmitting(true)
    setTicketResult(null)
    setTicketStatus(null)
    stopPolling()
    try {
      const res = await fetch(`${API}/tickets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ message: ticketMessage.trim() }),
      })
      if (res.status === 401) { onLogout(); return }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `Submission failed (${res.status})`)
      }
      const ticket = await res.json()
      setTicketResult(ticket)
      setTicketStatus(ticket)
      setTicketMessage('')
      if (!TERMINAL_TICKET_STATUSES.has(ticket.status)) {
        setTimeout(() => pollTicket(ticket.id), 2000)
        pollingRef.current = setInterval(() => pollTicket(ticket.id), 4000)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleReset = () => {
    stopPolling()
    setTicketResult(null)
    setTicketStatus(null)
    setTicketMessage('')
    setError('')
  }

  const status = ticketStatus?.status || ticketResult?.status
  const isTerminal = TERMINAL_TICKET_STATUSES.has(status)

  return (
    <div className="flex flex-col h-full">
      <div className="mb-6">
        <h2 className="text-xl font-bold text-gray-900">Support Tickets</h2>
        <p className="text-sm text-gray-500 mt-0.5">
          Submit an issue for async processing — our AI worker classifies and routes it automatically.
        </p>
      </div>

      {!ticketResult ? (
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6 max-w-2xl fade-in">
          <h3 className="font-semibold text-gray-900 mb-1">New ticket</h3>
          <p className="text-sm text-gray-500 mb-4">Describe your issue in as much detail as possible.</p>

          <textarea
            value={ticketMessage}
            onChange={e => setTicketMessage(e.target.value)}
            rows={5}
            className="w-full px-4 py-3 rounded-xl border border-gray-200 text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition resize-none"
            placeholder="e.g. I received a damaged product in my order #1042, I'd like a replacement…"
            disabled={submitting}
          />

          {error && <div className="mt-3"><Alert type="error">{error}</Alert></div>}

          <button
            onClick={handleSubmit}
            disabled={submitting || !ticketMessage.trim()}
            className="mt-4 flex items-center justify-center gap-2 w-full bg-indigo-600 hover:bg-indigo-700 disabled:bg-indigo-300 text-white py-2.5 rounded-xl text-sm font-semibold transition-colors"
          >
            {submitting ? <><Spinner size="sm" /><span>Submitting…</span></> : 'Submit Ticket'}
          </button>
        </div>
      ) : (
        <div className="max-w-2xl fade-in">
          {/* Success header */}
          <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-6">
            <div className="flex items-center justify-between mb-5">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-xl bg-indigo-50 flex items-center justify-center">
                  <Icon.Ticket />
                </div>
                <div>
                  <p className="font-semibold text-gray-900">Ticket #{ticketResult.id}</p>
                  <p className="text-xs text-gray-400">Submitted successfully</p>
                </div>
              </div>
              <button
                onClick={handleReset}
                className="flex items-center gap-1.5 text-xs font-medium text-indigo-600 hover:text-indigo-800 bg-indigo-50 hover:bg-indigo-100 px-3 py-1.5 rounded-lg transition-colors"
              >
                <Icon.Plus />
                New ticket
              </button>
            </div>

            {/* Status timeline */}
            <div className="space-y-3">
              <StatusRow label="Status">
                <div className="flex items-center gap-2">
                  <Badge color={ticketStatusColor(status)}>{status}</Badge>
                  {!isTerminal && <Spinner size="sm" />}
                </div>
              </StatusRow>
              {ticketStatus?.category && (
                <StatusRow label="Category">
                  <Badge color="blue">{ticketStatus.category}</Badge>
                </StatusRow>
              )}
              {ticketStatus?.priority && (
                <StatusRow label="Priority">
                  <Badge color={priorityColor(ticketStatus.priority)}>{ticketStatus.priority}</Badge>
                </StatusRow>
              )}
            </div>

            {/* Status description */}
            <div className="mt-5 pt-5 border-t border-gray-100">
              {status === 'PENDING' && (
                <p className="text-sm text-gray-500">Queued for background processing. This usually takes a few seconds.</p>
              )}
              {status === 'PROCESSED' && (
                <p className="text-sm text-emerald-600">✓ Classified and routed. A support agent will follow up with you.</p>
              )}
              {status === 'CLASSIFICATION_FAILED' && (
                <p className="text-sm text-red-600">Automatic classification failed. A support agent will review this manually.</p>
              )}
              {status === 'QUEUE_FAILED' && (
                <p className="text-sm text-red-600">Could not queue for processing. Please contact support directly.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

const StatusRow = ({ label, children }) => (
  <div className="flex items-center gap-4">
    <span className="text-xs font-medium text-gray-400 w-20 shrink-0">{label}</span>
    {children}
  </div>
)

// ── Approvals view ─────────────────────────────────────────────────────────
const ApprovalsView = ({ token, onLogout }) => {
  const [approvals, setApprovals] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [canAccess, setCanAccess] = useState(null)
  const [actionInProgress, setActionInProgress] = useState(null)
  const [rejectModal, setRejectModal] = useState(null)
  const [rejectReason, setRejectReason] = useState('')

  const fetchApprovals = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API}/approvals/pending`, { headers: { Authorization: `Bearer ${token}` } })
      if (res.status === 401) { onLogout(); return }
      if (res.status === 403) { setCanAccess(false); return }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to fetch (${res.status})`)
      }
      setApprovals(await res.json())
      setCanAccess(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [token, onLogout])

  useEffect(() => { fetchApprovals() }, [fetchApprovals])

  const handleApprove = async (id) => {
    setActionInProgress(id)
    setError('')
    try {
      const res = await fetch(`${API}/approvals/${id}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({}),
      })
      if (res.status === 401) { onLogout(); return }
      if (res.status === 409) { setError('Already resolved. Refreshing…'); setTimeout(fetchApprovals, 1500); return }
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `Approve failed (${res.status})`) }
      await fetchApprovals()
    } catch (err) {
      setError(err.message)
    } finally {
      setActionInProgress(null)
    }
  }

  const handleReject = async (id, reason) => {
    setActionInProgress(id)
    setError('')
    try {
      const res = await fetch(`${API}/approvals/${id}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ reason: reason || null }),
      })
      if (res.status === 401) { onLogout(); return }
      if (res.status === 409) { setError('Already resolved. Refreshing…'); setTimeout(fetchApprovals, 1500); return }
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `Reject failed (${res.status})`) }
      setRejectModal(null)
      setRejectReason('')
      await fetchApprovals()
    } catch (err) {
      setError(err.message)
    } finally {
      setActionInProgress(null)
    }
  }

  return (
    <div className="flex flex-col h-full">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-gray-900">Approval Queue</h2>
          <p className="text-sm text-gray-500 mt-0.5">Review and act on high-value action requests.</p>
        </div>
        {canAccess && (
          <button
            onClick={fetchApprovals}
            className="flex items-center gap-1.5 text-xs font-medium text-gray-600 hover:text-gray-900 bg-white border border-gray-200 hover:border-gray-300 px-3 py-1.5 rounded-lg transition-colors"
          >
            <Icon.Refresh />
            Refresh
          </button>
        )}
      </div>

      {error && <div className="mb-4"><Alert type="error">{error}</Alert></div>}

      {loading ? (
        <div className="flex items-center justify-center flex-1 py-24">
          <div className="text-center">
            <Spinner size="lg" className="mx-auto mb-3" />
            <p className="text-sm text-gray-500">Loading approvals…</p>
          </div>
        </div>
      ) : canAccess === false ? (
        <div className="flex-1 flex items-center justify-center fade-in">
          <div className="text-center max-w-sm">
            <div className="h-14 w-14 rounded-2xl bg-amber-50 flex items-center justify-center mx-auto mb-4">
              <svg className="w-7 h-7 text-amber-500" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
              </svg>
            </div>
            <p className="font-semibold text-gray-900 mb-1">Admin access required</p>
            <p className="text-sm text-gray-500">You don't have permission to view the approval queue.</p>
          </div>
        </div>
      ) : approvals.length === 0 ? (
        <div className="flex-1 flex items-center justify-center fade-in">
          <div className="text-center max-w-sm">
            <div className="h-14 w-14 rounded-2xl bg-emerald-50 flex items-center justify-center mx-auto mb-4">
              <Icon.Approvals />
            </div>
            <p className="font-semibold text-gray-900 mb-1">All clear</p>
            <p className="text-sm text-gray-500">No pending approvals at this time.</p>
          </div>
        </div>
      ) : (
        <div className="space-y-4 fade-in">
          <p className="text-xs text-gray-400 font-medium">
            {approvals.length} pending {approvals.length === 1 ? 'approval' : 'approvals'}
          </p>

          {approvals.map(a => (
            <div key={a.approval_id} className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <p className="font-semibold text-gray-900">{a.action_type}</p>
                  <p className="text-xs text-gray-400 mt-0.5">Order #{a.reference_id} · Customer #{a.customer_id}</p>
                </div>
                <Badge color="orange">{a.approval_status.replace(/_/g, ' ')}</Badge>
              </div>

              <div className="grid grid-cols-2 gap-3 mb-4">
                {a.amount !== null && (
                  <div className="bg-gray-50 rounded-xl p-3">
                    <p className="text-xs text-gray-400 mb-0.5">Amount</p>
                    <p className="font-bold text-gray-900">
                      ${a.amount.toLocaleString('en-US', { minimumFractionDigits: 2 })}
                    </p>
                  </div>
                )}
                <div className="bg-gray-50 rounded-xl p-3">
                  <p className="text-xs text-gray-400 mb-0.5">Created</p>
                  <p className="text-sm font-medium text-gray-700">
                    {new Date(a.created_at).toLocaleString('en-US', {
                      month: 'short', day: 'numeric',
                      hour: '2-digit', minute: '2-digit'
                    })}
                  </p>
                </div>
              </div>

              <div className="flex gap-2 pt-4 border-t border-gray-100">
                <button
                  onClick={() => { setRejectModal(a.approval_id); setRejectReason('') }}
                  disabled={actionInProgress === a.approval_id}
                  className="flex-1 py-2 rounded-xl border border-red-200 text-red-600 text-sm font-medium hover:bg-red-50 disabled:opacity-40 transition-colors"
                >
                  Reject
                </button>
                <button
                  onClick={() => handleApprove(a.approval_id)}
                  disabled={actionInProgress === a.approval_id}
                  className="flex-1 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium disabled:opacity-40 transition-colors"
                >
                  {actionInProgress === a.approval_id ? 'Processing…' : 'Approve'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Reject modal */}
      {rejectModal && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 z-50 fade-in">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6">
            <h3 className="font-bold text-gray-900 mb-1">Reject approval</h3>
            <p className="text-sm text-gray-500 mb-4">Optionally provide a reason for rejection.</p>
            <textarea
              value={rejectReason}
              onChange={e => setRejectReason(e.target.value)}
              rows={3}
              className="w-full px-3.5 py-3 rounded-xl border border-gray-200 text-sm focus:outline-none focus:ring-2 focus:ring-red-400 focus:border-red-400 resize-none"
              placeholder="Reason (optional)…"
            />
            <div className="mt-4 flex gap-2">
              <button
                onClick={() => { setRejectModal(null); setRejectReason('') }}
                disabled={actionInProgress === rejectModal}
                className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-700 text-sm font-medium hover:bg-gray-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => handleReject(rejectModal, rejectReason)}
                disabled={actionInProgress === rejectModal}
                className="flex-1 py-2.5 rounded-xl bg-red-600 hover:bg-red-700 text-white text-sm font-medium disabled:opacity-50 transition-colors"
              >
                {actionInProgress === rejectModal ? 'Rejecting…' : 'Confirm reject'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Root app ───────────────────────────────────────────────────────────────
const NAV = [
  { id: 'support',   label: 'Support',   Icon: Icon.Support },
  { id: 'tickets',   label: 'Tickets',   Icon: Icon.Ticket },
  { id: 'approvals', label: 'Approvals', Icon: Icon.Approvals },
]

export default function App() {
  const [token, setToken] = useState(null)
  const [view, setView]   = useState('support')

  const handleLogout = useCallback(() => {
    setToken(null)
    setView('support')
  }, [])

  if (!token) return <LoginScreen onLogin={setToken} />

  return (
    <div className="min-h-screen flex" style={{ background: '#f3f4f6' }}>
      {/* Sidebar */}
      <aside className="w-56 shrink-0 flex flex-col" style={{ background: '#1e1b4b' }}>
        {/* Logo */}
        <div className="flex items-center gap-2.5 px-5 py-5 border-b border-white/10">
          <div className="h-7 w-7 rounded-lg bg-indigo-400/20 flex items-center justify-center">
            <svg className="w-4 h-4 text-indigo-300" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 13.5l10.5-11.25L12 10.5h8.25L9.75 21.75 12 13.5H3.75z" />
            </svg>
          </div>
          <span className="font-bold text-white text-sm tracking-tight">SupportFlow</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-0.5">
          {NAV.map(({ id, label, Icon: NavIcon }) => {
            const active = view === id
            return (
              <button
                key={id}
                onClick={() => setView(id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all ${
                  active
                    ? 'bg-indigo-500/30 text-white'
                    : 'text-indigo-200/70 hover:text-indigo-100 hover:bg-white/5'
                }`}
              >
                <NavIcon />
                {label}
                {active && (
                  <span className="ml-auto h-1.5 w-1.5 rounded-full bg-indigo-400" />
                )}
              </button>
            )
          })}
        </nav>

        {/* Logout */}
        <div className="px-3 pb-4 border-t border-white/10 pt-3">
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium text-indigo-200/60 hover:text-indigo-100 hover:bg-white/5 transition-all"
          >
            <Icon.Logout />
            Sign out
          </button>
        </div>
      </aside>

      {/* Content */}
      <main className="flex-1 overflow-auto">
        <div className="max-w-3xl mx-auto px-6 py-8 h-full flex flex-col" style={{ minHeight: '100vh' }}>
          {view === 'support'   && <SupportView   key="support"   token={token} onLogout={handleLogout} />}
          {view === 'tickets'   && <TicketsView   key="tickets"   token={token} onLogout={handleLogout} />}
          {view === 'approvals' && <ApprovalsView key="approvals" token={token} onLogout={handleLogout} />}
        </div>
      </main>
    </div>
  )
}
