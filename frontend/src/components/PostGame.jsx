import { useEffect, useState } from 'react'
import ChatBox from './ChatBox'
import MatchSummary, { AdvantageGraphCard, FarmGraphCard } from './MatchSummary'
import {
  reviewMatch,
  chatMatch,
  fetchMatch,
  getChatHistory,
  getReviewHistory,
} from '../api'

const FIELD =
  'px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/25 transition-all'

export default function PostGame() {
  const [accountId, setAccountId] = useState(() => localStorage.getItem('accountId') || '')
  const [matchId, setMatchId] = useState('')
  const [review, setReview] = useState(null)
  const [toolTrace, setToolTrace] = useState([]) // tool names the fresh review called
  const [session, setSession] = useState(null) // {accountId, matchId} the loaded review belongs to
  const [chatSeed, setChatSeed] = useState([]) // prior chat turns from the server session
  const [match, setMatch] = useState(null) // raw OpenDota match for the scoreboard/graphs
  const [matchAccount, setMatchAccount] = useState(null)
  const [pastReviews, setPastReviews] = useState([])
  const [loading, setLoading] = useState(false)
  const [chatLoading, setChatLoading] = useState(false)
  const [error, setError] = useState(null)

  const refreshHistory = (acc) => {
    getReviewHistory(acc)
      .then((h) => setPastReviews(h.reviews || []))
      .catch(() => { })
  }

  useEffect(() => {
    const acc = localStorage.getItem('accountId')
    if (acc) {
      getReviewHistory(Number(acc))
        .then((h) => setPastReviews(h.reviews || []))
        .catch(() => { })
    }
  }, [])

  const loadMatch = async (acc, mid, fresh = false) => {
    if (loading) return
    setLoading(true)
    setError(null)
    setReview(null)
    setToolTrace([])
    setSession(null)
    setChatSeed([])
    setMatch(null)
    setMatchAccount(acc)
    // OpenDota answers in about a second; the scoreboard renders while the
    // review agent is still working.
    fetchMatch(mid)
      .then((m) => {
        if (m?.players) setMatch(m)
      })
      .catch(() => { })
    try {
      // A saved session means the review already ran: load it instead of
      // paying for a re-review, and seed the chat with its history. fresh
      // skips it to regenerate (and discards the saved chat server-side).
      const saved = fresh
        ? { found: false }
        : await getChatHistory(acc, mid).catch(() => ({ found: false }))
      if (saved.found) {
        setReview(saved.review)
        setChatSeed(saved.messages || [])
      } else {
        const data = await reviewMatch(acc, mid)
        if (!data.review) throw new Error(data.detail || 'no review returned')
        setReview(data.review)
        setToolTrace((data.toolTrace || []).map((s) => s.tool))
      }
      setSession({ accountId: acc, matchId: mid })
      refreshHistory(acc)
    } catch (err) {
      setError(err.message || 'Failed to get review. Is the API server running?')
    } finally {
      setLoading(false)
    }
  }

  const handleReview = (e) => {
    e.preventDefault()
    if (!accountId || !matchId) return
    localStorage.setItem('accountId', accountId)
    loadMatch(Number(accountId), Number(matchId))
  }

  const handleChat = async (message) => {
    setChatLoading(true)
    try {
      const data = await chatMatch(session.accountId, session.matchId, message)
      return data.reply
    } catch {
      return 'Failed to get a response. Is the API server running?'
    } finally {
      setChatLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <form
        onSubmit={handleReview}
        className="max-w-2xl mx-auto flex flex-col sm:flex-row gap-3 sm:items-end"
      >
        <div className="flex-1 flex flex-col gap-1.5">
          <label className="text-[10px] font-medium text-gray-600 uppercase tracking-widest">
            Account ID
          </label>
          <input
            type="text"
            inputMode="numeric"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value.replace(/\D/g, ''))}
            placeholder="e.g. 96183976"
            className={FIELD}
          />
        </div>
        <div className="flex-1 flex flex-col gap-1.5">
          <label className="text-[10px] font-medium text-gray-600 uppercase tracking-widest">
            Match ID
          </label>
          <input
            type="text"
            inputMode="numeric"
            value={matchId}
            onChange={(e) => setMatchId(e.target.value.replace(/\D/g, ''))}
            placeholder="e.g. 8869535334"
            className={FIELD}
          />
        </div>
        <button
          type="submit"
          disabled={loading || !accountId || !matchId}
          className="px-4 py-2 text-xs font-medium uppercase tracking-wider bg-indigo-500/15 border border-indigo-500/25 hover:bg-indigo-500/25 text-indigo-300 rounded-lg transition-all cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
        >
          {loading ? 'Reviewing...' : 'Review Match'}
        </button>
      </form>

      {pastReviews.length > 0 && (
        <div className="max-w-2xl mx-auto space-y-1.5">
          <div className="text-[10px] font-medium text-gray-600 uppercase tracking-widest">
            Recent Reviews
          </div>
          {pastReviews.map((h) => (
            <button
              key={h.matchId}
              disabled={loading}
              onClick={() => {
                setMatchId(String(h.matchId))
                loadMatch(Number(accountId), h.matchId)
              }}
              className="w-full flex gap-2 min-w-0 text-left px-3 py-2 rounded-lg border border-white/10 bg-white/[0.03] hover:bg-white/5 transition-colors cursor-pointer text-xs disabled:opacity-50"
            >
              <span className="text-indigo-300 shrink-0">{h.matchId}</span>
              <span className="text-gray-500 truncate">{h.snippet}</span>
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="max-w-2xl mx-auto rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {loading && (
        <p className="text-sm text-gray-600 text-center">
          Analyzing match... this can take a minute.
        </p>
      )}

      {/* Cards tile left-to-right, wrapping to the next row when full */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        {match && (
          <div className="lg:col-span-2">
            <MatchSummary match={match} accountId={matchAccount} />
          </div>
        )}

        {match?.radiant_gold_adv?.length > 1 && <AdvantageGraphCard match={match} accountId={matchAccount} />}

        {match && <FarmGraphCard match={match} accountId={matchAccount} />}

        {review && session && (
          <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[10px] font-medium text-gray-600 uppercase tracking-widest">
                Coach Review
              </div>
              <button
                onClick={() => loadMatch(session.accountId, session.matchId, true)}
                disabled={loading}
                className="text-[10px] font-medium uppercase tracking-widest text-indigo-300/70 hover:text-indigo-300 disabled:opacity-30 cursor-pointer"
              >
                Re-review
              </button>
            </div>
            <pre className="whitespace-pre-wrap font-[inherit] text-sm text-gray-300 leading-relaxed">
              {review}
            </pre>
            {toolTrace.length > 0 && (
              <details className="mt-3">
                <summary className="text-[10px] text-gray-600 uppercase tracking-widest cursor-pointer hover:text-gray-500">
                  Checked {toolTrace.length} data sources
                </summary>
                <div className="mt-1 text-xs text-gray-500">
                  {[...new Set(toolTrace)].join(', ')}
                </div>
              </details>
            )}
          </div>
        )}

        {session && (
          <ChatBox
            key={`${session.accountId}-${session.matchId}`}
            onSend={handleChat}
            loading={chatLoading}
            initialMessages={chatSeed}
            subtitle="Ask about this match"
            emptyText="Ask about builds, timings, deaths..."
            placeholder="Ask about this match..."
          />
        )}
      </div>
    </div>
  )
}
