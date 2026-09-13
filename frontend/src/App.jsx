import { useState } from 'react'

function App() {
  const [token, setToken] = useState(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [sending, setSending] = useState(false)
  const [response, setResponse] = useState(null)
  const [sendError, setSendError] = useState('')

  const handleLogin = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const response = await fetch('http://localhost:8001/auth/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ email, password }),
      })

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        throw new Error(errorData.detail || 'Login failed')
      }

      const data = await response.json()
      setToken(data.access_token)
      setEmail('')
      setPassword('')
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleSend = async () => {
    if (!message.trim()) return
    
    setSendError('')
    setSending(true)
    setResponse(null)

    try {
      const res = await fetch('http://localhost:8001/support/process', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify({ message: message.trim() }),
      })

      if (res.status === 401) {
        // Token expired or invalid - log out automatically
        setToken(null)
        setSendError('Session expired. Please log in again.')
        return
      }

      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}))
        throw new Error(errorData.detail || `Request failed with status ${res.status}`)
      }

      const data = await res.json()
      setResponse(data)
      setMessage('') // Clear textarea on success
    } catch (err) {
      setSendError(err.message)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center p-4">
      <div className="bg-white rounded-lg shadow-md p-8 w-full max-w-md">
        {!token ? (
          <>
            <h1 className="text-2xl font-bold mb-6 text-center text-gray-800">
              Support Portal Login
            </h1>
            
            <form onSubmit={handleLogin} className="space-y-4">
              <div>
                <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="you@example.com"
                />
              </div>

              <div>
                <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="••••••••"
                />
              </div>

              {error && (
                <div className="text-red-600 text-sm bg-red-50 p-3 rounded-md">
                  {error}
                </div>
              )}

              <button
                type="submit"
                disabled={loading}
                className="w-full bg-blue-600 text-white py-2 rounded-md hover:bg-blue-700 disabled:bg-blue-400 disabled:cursor-not-allowed transition-colors"
              >
                {loading ? 'Logging in...' : 'Log In'}
              </button>
            </form>
          </>
        ) : (
          <>
            <h1 className="text-2xl font-bold mb-6 text-center text-gray-800">
              Submit Support Request
            </h1>

            <div className="space-y-4">
              <div>
                <label htmlFor="message" className="block text-sm font-medium text-gray-700 mb-1">
                  Your Message
                </label>
                <textarea
                  id="message"
                  value={message}
                  onChange={(e) => setMessage(e.target.value)}
                  rows={6}
                  className="w-full px-3 py-2 border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500"
                  placeholder="Describe your issue..."
                />
              </div>

              {sendError && (
                <div className="text-red-600 text-sm bg-red-50 p-3 rounded-md">
                  {sendError}
                </div>
              )}

              <button
                type="button"
                onClick={handleSend}
                disabled={sending || !message.trim()}
                className="w-full bg-green-600 text-white py-2 rounded-md hover:bg-green-700 disabled:bg-green-400 disabled:cursor-not-allowed transition-colors"
              >
                {sending ? 'Sending...' : 'Send'}
              </button>

              {response && (
                <div className="mt-6 border border-gray-200 rounded-lg p-4 bg-gray-50 space-y-4">
                  {/* Main Response */}
                  <div className="bg-white p-4 rounded-md border border-gray-200">
                    <h3 className="text-sm font-semibold text-gray-700 mb-2">Response</h3>
                    <p className="text-gray-800">{response.final_response}</p>
                  </div>

                  {/* Classification */}
                  {response.classification && (
                    <div className="flex flex-wrap gap-2">
                      {response.classification.category && (
                        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                          {response.classification.category}
                        </span>
                      )}
                      {response.classification.priority && (
                        <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          response.classification.priority === 'urgent' 
                            ? 'bg-red-100 text-red-800'
                            : response.classification.priority === 'high'
                            ? 'bg-orange-100 text-orange-800'
                            : response.classification.priority === 'medium'
                            ? 'bg-yellow-100 text-yellow-800'
                            : 'bg-gray-100 text-gray-800'
                        }`}>
                          {response.classification.priority}
                        </span>
                      )}
                      {response.classification.sentiment && (
                        <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                          response.classification.sentiment === 'positive'
                            ? 'bg-green-100 text-green-800'
                            : response.classification.sentiment === 'negative'
                            ? 'bg-red-100 text-red-800'
                            : 'bg-gray-100 text-gray-800'
                        }`}>
                          {response.classification.sentiment}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Approval Status */}
                  {response.approval_status && (
                    <div>
                      <span className={`inline-flex items-center px-3 py-1 rounded-md text-sm font-medium ${
                        response.approval_status === 'PENDING_APPROVAL'
                          ? 'bg-orange-100 text-orange-800 border border-orange-200'
                          : response.approval_status === 'APPROVED'
                          ? 'bg-green-100 text-green-800 border border-green-200'
                          : response.approval_status === 'REJECTED'
                          ? 'bg-red-100 text-red-800 border border-red-200'
                          : 'bg-gray-100 text-gray-800 border border-gray-200'
                      }`}>
                        {response.approval_status.replace('_', ' ')}
                      </span>
                    </div>
                  )}

                  {/* Tool Calls */}
                  {response.tool_calls && response.tool_calls.length > 0 && (
                    <div className="bg-white p-3 rounded-md border border-gray-200">
                      <h4 className="text-sm font-semibold text-gray-700 mb-2">Actions Taken</h4>
                      <div className="space-y-2">
                        {response.tool_calls.map((tool, idx) => (
                          <div key={idx} className="border border-gray-200 rounded p-2 text-sm">
                            <div className="font-semibold text-gray-800 mb-1">
                              {tool.tool_name}
                            </div>
                            <div className="flex gap-3 mb-2 text-xs">
                              <span className="flex items-center gap-1">
                                {tool.validation_passed ? (
                                  <span className="text-green-600">✓</span>
                                ) : (
                                  <span className="text-red-600">✗</span>
                                )}
                                <span className="text-gray-600">Validated</span>
                              </span>
                              <span className="flex items-center gap-1">
                                {tool.authorization_passed ? (
                                  <span className="text-green-600">✓</span>
                                ) : (
                                  <span className="text-red-600">✗</span>
                                )}
                                <span className="text-gray-600">Authorized</span>
                              </span>
                              <span className="flex items-center gap-1">
                                {tool.backend_executed ? (
                                  <span className="text-green-600">✓</span>
                                ) : (
                                  <span className="text-red-600">✗</span>
                                )}
                                <span className="text-gray-600">Executed</span>
                              </span>
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

                  {/* Retrieved Documents */}
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

              <button
                type="button"
                onClick={() => {
                  setToken(null)
                  setResponse(null)
                  setMessage('')
                  setSendError('')
                }}
                className="w-full bg-gray-200 text-gray-700 py-2 rounded-md hover:bg-gray-300 transition-colors text-sm"
              >
                Log Out
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default App
