import { useState, useCallback, useEffect, useRef } from 'react'

const API = 'http://localhost:8001'

// Ticket status values that mean processing is complete
const TERMINAL_TICKET_STATUSES = new Set(['PROCESSED', 'CLASSIFICATION_FAILED', 'QUEUE_FAILED'])

function App() {
  const [token, setToken] = useState(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // Navigation
  const [currentView, setCurrentView] = useState('support') // 'support' | 'tickets' | 'approvals'

  // ── Support / Chat state ───────────────────────────────────────────
  const [message, setMessage] = useState('')
  const [sending, setSending] = useState(false)
  const [response, setResponse] = useState(null)
  const [sendError, setSendError] = useState('')

  // ── Ticket submission state ────────────────────────────────────────
  const [ticketMessage, setTicketMessage] = useState('')
  const [ticketSubmitting, setTicketSubmitting] = useState(false)
  const [ticketResult, setTicketResult] = useState(null)   // created ticket
  const [ticketError, setTicketError] = useState('')
  const [ticketStatus, setTicketStatus] = useState(null)   // polled ticket detail
  const pollingRef = useRef(null)

  // ── Approvals state ───────────────────────────────────────────────
  const [canAccessApprovals, setCanAccessApprovals] = useState(null)
  const [approvals, setApprovals] = useState([])
  const [approvalsLoading, setApprovalsLoading] = useState(false)
  const [approvalsError, setApprovalsError] = useState('')
  const [actionInProgress, setActionInProgress] = useState(null)
  const [rejectModalOpen, setRejectModalOpen] = useState(null)
  const [rejectReason, setRejectReason] = useState('')

  // Stop polling on unmount or when ticket reaches terminal state
  const stopPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current)
      pollingRef.current = null
    }
  }, [])

  useEffect(() => {
    return () => stopPolling()
  }, [stopPolling])

  // ── Auth ───────────────────────────────────────────────────────────
  const handleLogin = async (e) => {
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
        throw new Error(d.detail || 'Login failed')
      }
      const data = await res.json()
      setToken(data.access_token)
      setEmail('')
      setPassword('')
      setCanAccessApprovals(null)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleLogout = () => {
    stopPolling()
    setToken(null)
    setResponse(null)
    setMessage('')
    setSendError('')
    setTicketResult(null)
    setTicketStatus(null)
    setTicketMessage('')
    setTicketError('')
    setCurrentView('support')
    setCanAccessApprovals(null)
    setApprovals([])
    setApprovalsError('')
  }

  // ── Support / Chat ─────────────────────────────────────────────────
  const handleSend = async () => {
    if (!message.trim()) return
    setSendError('')
    setSending(true)
    setResponse(null)
    try {
      const res = await fetch(`${API}/support/process`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ message: message.trim() }),
      })
      if (res.status === 401) { handleLogout(); setSendError('Session expired. Please log in again.'); return }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `Request failed (${res.status})`)
      }
      const data = await res.json()
      setResponse(data)
      setMessage('')
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSending(false)
    }
  }

  // ── Ticket creation & polling ──────────────────────────────────────
  const pollTicket = useCallback(async (ticketId) => {
    try {
      const res = await fetch(`${API}/tickets/${ticketId}`, {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (res.status === 401) { handleLogout(); return }
      if (!res.ok) return  // silent — keep polling
      const data = await res.json()
      setTicketStatus(data)
      if (TERMINAL_TICKET_STATUSES.has(data.status)) {
        stopPolling()
      }
    } catch {
      // network error — keep polling silently
    }
  }, [token, stopPolling]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleTicketSubmit = async () => {
    if (!ticketMessage.trim()) return
    setTicketError('')
    setTicketSubmitting(true)
    setTicketResult(null)
    setTicketStatus(null)
    stopPolling()

    try {
      const res = await fetch(`${API}/tickets`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ message: ticketMessage.trim() }),
      })
      if (res.status === 401) { handleLogout(); setTicketError('Session expired. Please log in again.'); return }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `Submission failed (${res.status})`)
      }
      const ticket = await res.json()
      setTicketResult(ticket)
      setTicketStatus(ticket)
      setTicketMessage('')

      // Only start polling if not already terminal
      if (!TERMINAL_TICKET_STATUSES.has(ticket.status)) {
        // Initial fetch after a short delay, then every 4 seconds
        setTimeout(() => pollTicket(ticket.id), 2000)
        pollingRef.current = setInterval(() => pollTicket(ticket.id), 4000)
      }
    } catch (err) {
      setTicketError(err.message)
    } finally {
      setTicketSubmitting(false)
    }
  }

  const handleNewTicket = () => {
    stopPolling()
    setTicketResult(null)
    setTicketStatus(null)
    setTicketMessage('')
    setTicketError('')
  }

  // ── Approvals ──────────────────────────────────────────────────────
  const fetchApprovals = useCallback(async () => {
    setApprovalsLoading(true)
    setApprovalsError('')
    try {
      const res = await fetch(`${API}/approvals/pending`, {
        headers: { 'Authorization': `Bearer ${token}` },
      })
      if (res.status === 401) { handleLogout(); return }
      if (res.status === 403) { setCanAccessApprovals(false); setApprovalsError('Admin access required'); return }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}))
        throw new Error(d.detail || `Failed to fetch approvals (${res.status})`)
      }
      const data = await res.json()
      setApprovals(data)
      setCanAccessApprovals(true)
    } catch (err) {
      setApprovalsError(err.message)
    } finally {
      setApprovalsLoading(false)
    }
  }, [token]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleApprove = async (approvalId) => {
    setActionInProgress(approvalId)
    setApprovalsError('')
    try {
      const res = await fetch(`${API}/approvals/${approvalId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({}),
      })
      if (res.status === 401) { handleLogout(); return }
      if (res.status === 403) { setApprovalsError('Admin access required'); return }
      if (res.status === 409) { setApprovalsError('Already resolved. Refreshing...'); setTimeout(fetchApprovals, 1500); return }
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `Approve failed (${res.status})`) }
      await fetchApprovals()
    } catch (err) {
      setApprovalsError(err.message)
    } finally {
      setActionInProgress(null)
    }
  }

  const handleReject = async (approvalId, reason) => {
    setActionInProgress(approvalId)
    setApprovalsError('')
    try {
      const res = await fetch(`${API}/approvals/${approvalId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
        body: JSON.stringify({ reason: reason || null }),
      })
      if (res.status === 401) { handleLogout(); return }
      if (res.status === 403) { setApprovalsError('Admin access required'); return }
      if (res.status === 409) { setApprovalsError('Already resolved. Refreshing...'); setTimeout(fetchApprovals, 1500); return }
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || `Reject failed (${res.status})`) }
      setRejectModalOpen(null)
      setRejectReason('')
      await fetchApprovals()
    } catch (err) {
      setApprovalsError(err.message)
    } finally {
      setActionInProgress(null)
    }
  }

  const handleViewChange = (view) => {
    setCurrentView(view)
    if (view === 'approvals' && token && approvals.length === 0 && !approvalsLoading && canAccessApprovals === null) {
      fetchApprovals()
    }
  }

  // ── Helpers ────────────────────────────────────────────────────────
  const priorityColors = {
    URGENT: 'bg-red-100 text-red-800',
    HIGH: 'bg-orange-100 text-orange-800',
    MEDIUM: 'bg-yellow-100 text-yellow-800',
    LOW: 'bg-gray-100 text-gray-800',
    urgent: 'bg-red-100 text-red-800',
    high: 'bg-orange-100 text-orange-800',
    medium: 'bg-yellow-100 text-yellow-800',
    low: 'bg-gray-100 text-gray-800',
  }
  const statusColors = {
    PENDING: 'bg-yellow-100 text-yellow-800',
    PROCESSED: 'bg-green-100 text-green-800',
    CLASSIFICATION_FAILED: 'bg-red-100 text-red-800',
    QUEUE_FAILED: 'bg-red-100 text-red-800',
  }

  // ── Render ─────────────────────────────────────────────────────────
  if (!token) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
        <div className="bg-white rounded-lg shadow-md p-8 w-full max-w-md">
          <h1 className="text-2xl font-bold mb-6 text-center text-gray-800">Support Portal Login</h1>
          <form onSubmit={handleLogin} className="space-y-4">
            <div>
              <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">Email</label>
              <input
                id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="you@example.com"
              />
            </div>
            <div>
              <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">Password</label>
              <input
                id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                placeholder="••••••••"
              />
            </div>
            {error && <div className="text-red-600 text-sm bg-red-50 p-3 rounded-md">{error}</div>}
            <button
              type="submit" disabled={loading}
              className="w-full bg-blue-600 text-white py-2 rounded-md hover:bg-blue-700 disabled:bg-blue-400 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? 'Logging in...' : 'Log In'}
            </button>
          </form>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between items-center h-16">
            <h1 className="text-xl font-bold text-gray-900">SupportFlow</h1>
            <button onClick={handleLogout} className="text-sm text-gray-600 hover:text-gray-900 font-medium">
              Logout
            </button>
          </div>
        </div>
      </header>

      <div className="flex-1 flex" style={{ minHeight: 'calc(100vh - 64px)' }}>
        {/* Sidebar */}
        <aside className="w-48 bg-white border-r border-gray-200">
          <nav className="p-4 space-y-1">
            {[
              { id: 'support', label: 'Support' },
              { id: 'tickets', label: 'Tickets' },
              { id: 'approvals', label: 'Approvals' },
            ].map(({ id, label }) => (
              <button
                key={id}
                onClick={() => handleViewChange(id)}
                className={`w-full text-left px-3 py-2 rounded-md text-sm font-medium transition-colors ${
                  currentView === id ? 'bg-blue-50 text-blue-700' : 'text-gray-700 hover:bg-gray-50'
                }`}
              >
                {label}
              </button>
            ))}
          </nav>
        </aside>

        {/* Main content */}
        <main className="flex-1 overflow-auto">
          <div className="max-w-4xl mx-auto p-6">

            {/* ── Support / Chat view ──────────────────────────────── */}
            {currentView === 'support' && (
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                <h2 className="text-2xl font-bold mb-1 text-gray-800">Chat with Support</h2>
                <p className="text-sm text-gray-500 mb-6">
                  Get an immediate response to your question or request an action on your account.
                </p>

                <div className="space-y-4">
                  <div>
                    <label htmlFor="message" className="block text-sm font-medium text-gray-700 mb-1">
                      Your message
                    </label>
                    <textarea
                      id="message" value={message} onChange={(e) => setMessage(e.target.value)} rows={6}
                      className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                      placeholder="Ask a question, check an order status, request a refund…"
                    />
                  </div>

                  {sendError && (
                    <div className="text-red-600 text-sm bg-red-50 p-3 rounded-md border border-red-200">{sendError}</div>
                  )}

                  <button
                    type="button" onClick={handleSend} disabled={sending || !message.trim()}
                    className="w-full bg-green-600 text-white py-2 rounded-md hover:bg-green-700 disabled:bg-green-400 disabled:cursor-not-allowed transition-colors"
                  >
                    {sending ? 'Sending…' : 'Send'}
                  </button>

                  {response && (
                    <div className="mt-6 border border-gray-200 rounded-lg p-4 bg-gray-50 space-y-4">
                      {/* Final response */}
                      <div className="bg-white p-4 rounded-md border border-gray-200">
                        <h3 className="text-sm font-semibold text-gray-700 mb-2">Response</h3>
                        <p className="text-gray-800">{response.final_response}</p>
                      </div>

                      {/* Classification badges */}
                      {response.classification && (
                        <div className="flex flex-wrap gap-2">
                          {response.classification.category && (
                            <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                              {response.classification.category}
                            </span>
                          )}
                          {response.classification.priority && (
                            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${priorityColors[response.classification.priority] || 'bg-gray-100 text-gray-800'}`}>
                              {response.classification.priority}
                            </span>
                          )}
                          {response.classification.sentiment && (
                            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                              response.classification.sentiment === 'positive' ? 'bg-green-100 text-green-800'
                              : response.classification.sentiment === 'negative' ? 'bg-red-100 text-red-800'
                              : 'bg-gray-100 text-gray-800'
                            }`}>
                              {response.classification.sentiment}
                            </span>
                          )}
                        </div>
                      )}

                      {/* Approval status */}
                      {response.approval_status && (
                        <span className={`inline-flex items-center px-3 py-1 rounded-md text-sm font-medium border ${
                          response.approval_status === 'PENDING_APPROVAL' ? 'bg-orange-100 text-orange-800 border-orange-200'
                          : response.approval_status === 'APPROVED' ? 'bg-green-100 text-green-800 border-green-200'
                          : response.approval_status === 'REJECTED' ? 'bg-red-100 text-red-800 border-red-200'
                          : 'bg-gray-100 text-gray-800 border-gray-200'
                        }`}>
                          {response.approval_status.replace('_', ' ')}
                        </span>
                      )}

                      {/* Tool calls */}
                      {response.tool_calls && response.tool_calls.length > 0 && (
                        <div className="bg-white p-3 rounded-md border border-gray-200">
                          <h4 className="text-sm font-semibold text-gray-700 mb-2">Actions Taken</h4>
                          <div className="space-y-2">
                            {response.tool_calls.map((tool, idx) => (
                              <div key={idx} className="border border-gray-200 rounded p-2 text-sm">
                                <div className="font-semibold text-gray-800 mb-1">{tool.tool_name}</div>
                                <div className="flex gap-3 mb-2 text-xs">
                                  {[
                                    [tool.validation_passed, 'Validated'],
                                    [tool.authorization_passed, 'Authorized'],
                                    [tool.backend_executed, 'Executed'],
                                  ].map(([ok, label]) => (
                                    <span key={label} className="flex items-center gap-1">
                                      <span className={ok ? 'text-green-600' : 'text-red-600'}>{ok ? '✓' : '✗'}</span>
                                      <span className="text-gray-600">{label}</span>
                                    </span>
                                  ))}
                                </div>
                                {tool.tool_args && Object.keys(tool.tool_args).length > 0 && (
                                  <div className="text-xs text-gray-600 space-y-0.5">
                                    {Object.entries(tool.tool_args).map(([key, value]) => (
                                      <div key={key}>
                                        <span className="font-medium">{key}:</span>{' '}
                                        <span>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      {/* Retrieved documents */}
                      {response.retrieved_documents && response.retrieved_documents.length > 0 && (
                        <div className="bg-white p-3 rounded-md border border-gray-200">
                          <h4 className="text-sm font-semibold text-gray-700 mb-2">Knowledge Base Articles</h4>
                          <ul className="space-y-1 text-sm text-gray-600">
                            {response.retrieved_documents.map((doc, idx) => (
                              <li key={idx} className="flex items-start">
                                <span className="text-gray-400 mr-2">📄</span>
                                <span>{doc.title || doc}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* ── Tickets view ─────────────────────────────────────── */}
            {currentView === 'tickets' && (
              <div className="space-y-6">
                <div>
                  <h2 className="text-2xl font-bold text-gray-800">Support Tickets</h2>
                  <p className="text-sm text-gray-500 mt-1">
                    Submit an issue for background processing. A support agent will classify and review your ticket.
                  </p>
                </div>

                {!ticketResult ? (
                  /* ── Submission form ───────────────────────────── */
                  <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                    <h3 className="text-lg font-semibold text-gray-800 mb-4">Create a Ticket</h3>

                    <div className="space-y-4">
                      <div>
                        <label htmlFor="ticket-message" className="block text-sm font-medium text-gray-700 mb-1">
                          Describe your issue
                        </label>
                        <textarea
                          id="ticket-message" value={ticketMessage}
                          onChange={(e) => setTicketMessage(e.target.value)} rows={5}
                          className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                          placeholder="Describe your issue in detail…"
                          disabled={ticketSubmitting}
                        />
                      </div>

                      {ticketError && (
                        <div className="text-red-600 text-sm bg-red-50 p-3 rounded-md border border-red-200">
                          {ticketError}
                        </div>
                      )}

                      <button
                        type="button" onClick={handleTicketSubmit}
                        disabled={ticketSubmitting || !ticketMessage.trim()}
                        className="w-full bg-blue-600 text-white py-2 rounded-md hover:bg-blue-700 disabled:bg-blue-400 disabled:cursor-not-allowed transition-colors"
                      >
                        {ticketSubmitting ? 'Submitting…' : 'Submit Ticket'}
                      </button>
                    </div>
                  </div>
                ) : (
                  /* ── Ticket result + status polling ────────────── */
                  <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                    <div className="flex items-center justify-between mb-4">
                      <h3 className="text-lg font-semibold text-gray-800">
                        Ticket #{ticketResult.id} created
                      </h3>
                      <button
                        onClick={handleNewTicket}
                        className="text-sm text-blue-600 hover:text-blue-800 font-medium"
                      >
                        + New Ticket
                      </button>
                    </div>

                    {/* Status card */}
                    <div className="border border-gray-100 rounded-lg p-4 bg-gray-50 space-y-3">
                      {/* Status row */}
                      <div className="flex items-center gap-3">
                        <span className="text-sm font-medium text-gray-600 w-20">Status</span>
                        <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${statusColors[ticketStatus?.status] || 'bg-gray-100 text-gray-800'}`}>
                          {ticketStatus?.status || ticketResult.status}
                        </span>
                        {/* Spinner while waiting for terminal state */}
                        {ticketStatus && !TERMINAL_TICKET_STATUSES.has(ticketStatus.status) && (
                          <span className="inline-block animate-spin rounded-full h-4 w-4 border-2 border-gray-300 border-t-blue-600" title="Processing…" />
                        )}
                      </div>

                      {/* Category row — only shown once worker has set it */}
                      {ticketStatus?.category && (
                        <div className="flex items-center gap-3">
                          <span className="text-sm font-medium text-gray-600 w-20">Category</span>
                          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                            {ticketStatus.category}
                          </span>
                        </div>
                      )}

                      {/* Priority row — only shown once worker has set it */}
                      {ticketStatus?.priority && (
                        <div className="flex items-center gap-3">
                          <span className="text-sm font-medium text-gray-600 w-20">Priority</span>
                          <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${priorityColors[ticketStatus.priority] || 'bg-gray-100 text-gray-800'}`}>
                            {ticketStatus.priority}
                          </span>
                        </div>
                      )}
                    </div>

                    {/* Status descriptions */}
                    {ticketStatus?.status === 'PENDING' && (
                      <p className="mt-3 text-sm text-gray-500">
                        Your ticket has been submitted and is queued for background processing.
                      </p>
                    )}
                    {ticketStatus?.status === 'PROCESSED' && (
                      <p className="mt-3 text-sm text-green-700">
                        Your ticket has been processed. A support agent will follow up with you.
                      </p>
                    )}
                    {ticketStatus?.status === 'CLASSIFICATION_FAILED' && (
                      <p className="mt-3 text-sm text-red-600">
                        Automatic processing encountered an issue. A support agent will review your ticket manually.
                      </p>
                    )}
                    {ticketStatus?.status === 'QUEUE_FAILED' && (
                      <p className="mt-3 text-sm text-red-600">
                        Your ticket was created but could not be queued for processing. Please contact support directly.
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}

            {/* ── Approvals view ───────────────────────────────────── */}
            {currentView === 'approvals' && (
              <div>
                <div className="mb-6">
                  <h2 className="text-2xl font-bold text-gray-900">Approval Queue</h2>
                  <p className="text-sm text-gray-600 mt-1">Review and process pending approval requests</p>
                </div>

                {approvalsError && (
                  <div className={`mb-4 p-4 rounded-md border ${
                    canAccessApprovals === false
                      ? 'bg-yellow-50 border-yellow-200 text-yellow-800'
                      : 'bg-red-50 border-red-200 text-red-600'
                  }`}>
                    {approvalsError}
                  </div>
                )}

                {approvalsLoading ? (
                  <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-12 text-center">
                    <div className="inline-block animate-spin rounded-full h-8 w-8 border-4 border-gray-300 border-t-blue-600" />
                    <p className="mt-4 text-gray-600">Loading approvals…</p>
                  </div>
                ) : canAccessApprovals === false ? (
                  <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-12 text-center">
                    <div className="text-yellow-600 text-5xl mb-4">⚠️</div>
                    <h3 className="text-lg font-semibold text-gray-900 mb-2">Admin Access Required</h3>
                    <p className="text-gray-600">You do not have permission to view the approval queue.</p>
                  </div>
                ) : approvals.length === 0 ? (
                  <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-12 text-center">
                    <div className="text-green-600 text-5xl mb-4">✓</div>
                    <h3 className="text-lg font-semibold text-gray-900 mb-2">No Pending Approvals</h3>
                    <p className="text-gray-600">All approvals have been processed.</p>
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div className="text-sm text-gray-600 mb-4">
                      {approvals.length} pending {approvals.length === 1 ? 'approval' : 'approvals'}
                    </div>
                    {approvals.map((approval) => (
                      <div key={approval.approval_id} className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
                        <div className="flex justify-between items-start mb-4">
                          <div>
                            <h3 className="text-lg font-semibold text-gray-900 mb-1">{approval.action_type}</h3>
                            <p className="text-sm text-gray-600">Order #{approval.reference_id}</p>
                          </div>
                          <span className="inline-flex items-center px-3 py-1 rounded-full text-xs font-medium bg-orange-100 text-orange-800 border border-orange-200">
                            {approval.approval_status.replace('_', ' ')}
                          </span>
                        </div>
                        <div className="grid grid-cols-2 gap-4 mb-4 text-sm">
                          {approval.amount !== null && (
                            <div>
                              <span className="text-gray-600">Amount:</span>
                              <span className="ml-2 font-semibold text-gray-900">
                                ${approval.amount.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                              </span>
                            </div>
                          )}
                          <div>
                            <span className="text-gray-600">Customer ID:</span>
                            <span className="ml-2 font-mono text-gray-900">#{approval.customer_id}</span>
                          </div>
                          <div className="col-span-2">
                            <span className="text-gray-600">Created:</span>
                            <span className="ml-2 text-gray-900">
                              {new Date(approval.created_at).toLocaleString('en-US', {
                                year: 'numeric', month: 'short', day: 'numeric',
                                hour: '2-digit', minute: '2-digit'
                              })}
                            </span>
                          </div>
                        </div>
                        <div className="flex gap-3 pt-4 border-t border-gray-200">
                          <button
                            onClick={() => setRejectModalOpen(approval.approval_id)}
                            disabled={actionInProgress === approval.approval_id}
                            className="flex-1 px-4 py-2 bg-white border border-red-300 text-red-700 rounded-md hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium"
                          >
                            {actionInProgress === approval.approval_id ? 'Processing…' : 'Reject'}
                          </button>
                          <button
                            onClick={() => handleApprove(approval.approval_id)}
                            disabled={actionInProgress === approval.approval_id}
                            className="flex-1 px-4 py-2 bg-green-600 text-white rounded-md hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium"
                          >
                            {actionInProgress === approval.approval_id ? 'Processing…' : 'Approve'}
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

          </div>
        </main>
      </div>

      {/* Reject modal */}
      {rejectModalOpen && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Reject Approval</h3>
            <div className="mb-4">
              <label htmlFor="reject-reason" className="block text-sm font-medium text-gray-700 mb-2">
                Reason (optional)
              </label>
              <textarea
                id="reject-reason" value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} rows={3}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
                placeholder="Enter reason for rejection…"
              />
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => { setRejectModalOpen(null); setRejectReason('') }}
                disabled={actionInProgress === rejectModalOpen}
                className="flex-1 px-4 py-2 bg-gray-200 text-gray-700 rounded-md hover:bg-gray-300 disabled:opacity-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => handleReject(rejectModalOpen, rejectReason)}
                disabled={actionInProgress === rejectModalOpen}
                className="flex-1 px-4 py-2 bg-red-600 text-white rounded-md hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {actionInProgress === rejectModalOpen ? 'Rejecting…' : 'Confirm Reject'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default App
