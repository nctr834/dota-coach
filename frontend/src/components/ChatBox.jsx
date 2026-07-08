import { useState, useRef, useEffect } from 'react'

export default function ChatBox({ onSend, loading, disabled, subtitle, emptyText, placeholder, initialMessages }) {
  const [input, setInput] = useState('')
  const [messages, setMessages] = useState(initialMessages || [])
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const handleSubmit = async (e) => {
    e.preventDefault()
    const query = input.trim()
    if (!query || loading) return

    setMessages(prev => [...prev, { role: 'user', text: query }])
    setInput('')

    const response = await onSend(query)
    if (response) {
      setMessages(prev => [...prev, { role: 'assistant', text: response }])
    }
  }

  return (
    <div className="relative rounded-xl border border-white/10 bg-white/[0.02] overflow-hidden flex flex-col" style={{ height: '420px' }}>
      {/* Header */}
      <div className="px-4 py-3 border-b border-white/5 flex items-center gap-2">
        <div className={`w-2 h-2 rounded-full ${disabled ? 'bg-gray-600' : 'bg-indigo-500'}`} />
        <span className={`text-sm font-semibold uppercase tracking-wider ${disabled ? 'text-gray-600' : 'text-indigo-400'}`}>Coach</span>
        <span className="text-xs text-gray-600 ml-auto">{subtitle ?? (disabled ? 'Waiting for hero pick' : 'AI-powered draft advice')}</span>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 hero-list">
        {messages.length === 0 && !loading && (
          <div className="flex items-center justify-center h-full">
            <p className="text-sm text-gray-600 text-center">
              {emptyText ??
                (disabled
                  ? 'Select your team, role, and hero to start chatting with your coach'
                  : 'Ask about matchups, builds, game plans...')}
            </p>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] px-3 py-2 rounded-lg text-sm leading-relaxed ${
              msg.role === 'user'
                ? 'bg-indigo-500/15 border border-indigo-500/20 text-gray-200'
                : 'bg-white/[0.04] border border-white/5 text-gray-300'
            }`}>
              <pre className="whitespace-pre-wrap font-[inherit]">{msg.text}</pre>
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-white/[0.04] border border-white/5 rounded-lg px-3 py-2">
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-pulse" />
                <span className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-pulse" style={{ animationDelay: '0.2s' }} />
                <span className="w-1.5 h-1.5 rounded-full bg-gray-500 animate-pulse" style={{ animationDelay: '0.4s' }} />
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className={`p-3 border-t border-white/5 ${disabled ? 'opacity-40 pointer-events-none' : ''}`}>
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder={placeholder ?? (disabled ? 'Pick a hero first...' : 'Ask your coach...')}
            disabled={loading || disabled}
            className="flex-1 px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/25 transition-all disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={loading || disabled || !input.trim()}
            className="px-4 py-2 text-xs font-medium uppercase tracking-wider bg-indigo-500/15 border border-indigo-500/25 hover:bg-indigo-500/25 text-indigo-300 rounded-lg transition-all cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  )
}
