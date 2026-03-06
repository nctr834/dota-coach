import { useState, useEffect } from 'react'
import DraftGrid from './components/DraftGrid'
import PosSlot from './components/PosSlot'
import ChatBox from './components/ChatBox'
import { scoreTeams, rankPicks, processQuery } from './api'
// /apps/dota2/images/dota_react/abilities/ path base for ability icons
// /apps/dota2/images/dota_react/items/ path base for item icons
function App() {
  const [radiant, setRadiant] = useState(Array(5).fill(null))
  const [dire, setDire] = useState(Array(5).fill(null))
  const [score, setScore] = useState(null)
  const [suggestions, setSuggestions] = useState({})
  const [loading, setLoading] = useState(false)
  const [pos, setPos] = useState(null)
  const [mySide, setMySide] = useState(null)
  const [chatLoading, setChatLoading] = useState(false)
  const [picked, setPicked] = useState(false)

  const allPicked = [...radiant, ...dire].filter(Boolean)
  const roles = Array(5).fill().map((_, i) => i + 1)

  const setHero = (side, i, hero) => {
    const setter = side === 'radiant' ? setRadiant : setDire
    setter(prev => {
      const next = [...prev]
      next[i] = hero
      return next
    })
    setSuggestions({})
    if (i + 1 === pos) {
      setPicked(true)
    }
  }

  const clearHero = (side, i) => {
    const setter = side === 'radiant' ? setRadiant : setDire
    setter(prev => {
      const next = [...prev]
      if (i + 1 === pos) {
        setPicked(false)
      }
      setSuggestions({})
      next[i] = null
      return next
    })
  }

  const clearAll = () => {
    setRadiant(Array(5).fill(null))
    setDire(Array(5).fill(null))
    setPicked(false)
    setScore(null)
    setSuggestions({})
  }

  useEffect(() => {
    const radiantPicked = radiant.filter(Boolean)
    const direPicked = dire.filter(Boolean)
    if (radiantPicked.length === 0 || direPicked.length === 0) {
      setScore(null)
      return
    }
    scoreTeams(radiant, dire)
      .then(setScore)
      .catch(() => setScore(null))
  }, [radiant, dire])

  const handleSuggest = async (side, slotIndex) => {
    const pos = `${slotIndex + 1}`
    const team = side === 'radiant' ? radiant : dire
    const enemyTeam = side === 'radiant' ? dire : radiant
    const key = `${side}-${slotIndex}`

    setLoading(true)
    try {
      const data = await rankPicks(team, enemyTeam, pos)
      setSuggestions(prev => ({ ...prev, [key]: data.picks }))
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  const handleChat = async (query) => {
    let myTeam = null
    let pick = null
    if (mySide != null) {
      myTeam = mySide === 'radiant' ? radiant : dire
      const myHero = pos ? myTeam[pos - 1] : null
      pick = myHero ? myHero.displayName : ''
    }
    setChatLoading(true)
    try {
      const data = await processQuery(query, radiant, dire, pick, pos, mySide)
      return data.response
    } catch {
      return 'Failed to get a response. Is the API server running?'
    } finally {
      setChatLoading(false)
    }
  }

  const deltaLabel = score
    ? score.delta > 0 ? 'Radiant Favored' : score.delta < 0 ? 'Dire Favored' : 'Even Draft'
    : null

  return (
    <div className="min-h-screen bg-[#0a0e17] text-gray-100">
      {/* Header */}
      <header className="border-b border-white/5 bg-white/[0.02] backdrop-blur">
        <div className="max-w-7xl mx-auto px-6 py-5 flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">
            <span className="text-gray-400">Dota 2</span>{' '}
            <span className="text-white">Coach</span>
          </h1>
          <button
            onClick={clearAll}
            className="px-3 py-1.5 text-xs font-medium text-gray-500 hover:text-gray-300 border border-white/10 hover:border-white/20 rounded-md transition-colors cursor-pointer"
          >
            Reset Draft
          </button>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8 space-y-8">
        {/* Team + Role selectors */}
        <section className="flex flex-col sm:flex-row items-center justify-center gap-6">
          <div className="flex flex-col items-center gap-2">
            <span className="text-[10px] font-medium text-gray-600 uppercase tracking-widest">Your Team</span>
            <div className="inline-flex rounded-lg border border-white/10 bg-white/[0.03] p-1 gap-0.5">
              <button
                onClick={() => setMySide('radiant')}
                className={`px-4 py-2 text-xs font-medium rounded-md transition-all cursor-pointer ${mySide === 'radiant'
                  ? 'bg-green-500/15 text-green-400 shadow-sm'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'
                  }`}
              >
                Radiant
              </button>
              <button
                onClick={() => setMySide('dire')}
                className={`px-4 py-2 text-xs font-medium rounded-md transition-all cursor-pointer ${mySide === 'dire'
                  ? 'bg-red-500/15 text-red-400 shadow-sm'
                  : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'
                  }`}
              >
                Dire
              </button>
            </div>
          </div>

          <div className="w-px h-8 bg-white/10 hidden sm:block" />

          <div className="flex flex-col items-center gap-2">
            <span className="text-[10px] font-medium text-gray-600 uppercase tracking-widest">Your Role</span>
            <PosSlot
              roles={roles}
              selectedPos={pos}
              onSelect={(r) => setPos(r)}
            />
          </div>
        </section>

        {/* Score banner */}
        {score && (
          <section className="max-w-lg mx-auto">
            <div className="relative overflow-hidden rounded-xl border border-white/10 bg-white/[0.03] backdrop-blur p-5">
              <div className="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-green-500/0 via-green-500/50 to-red-500/50" />
              <div className="flex items-center justify-between mb-3">
                <div className="text-sm font-medium text-green-400">{score.radiantScore}</div>
                <div className="text-[10px] font-medium text-gray-600 uppercase tracking-widest">Advantage</div>
                <div className="text-sm font-medium text-red-400">{score.direScore}</div>
              </div>
              <div className="flex items-center gap-3">
                <div className="flex-1 h-1.5 rounded-full bg-gray-800 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.min(100, Math.max(0, 50 + score.delta))}%`,
                      background: score.delta >= 0
                        ? 'linear-gradient(90deg, #22c55e, #4ade80)'
                        : 'linear-gradient(90deg, #ef4444, #f87171)',
                    }}
                  />
                </div>
              </div>
              <div className={`text-center mt-3 text-sm font-semibold ${score.delta > 0 ? 'text-green-400' : score.delta < 0 ? 'text-red-400' : 'text-gray-400'}`}>
                {deltaLabel} {score.delta !== 0 && typeof score.delta === 'number' && <span className="text-gray-500 font-normal">({score.delta > 0 ? '+' : ''}{score.delta.toFixed(1)})</span>}
              </div>
            </div>
          </section>
        )}

        {/* Draft grids + Chat */}
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1fr_1fr] gap-6">
          <DraftGrid
            side="radiant"
            heroes={radiant}
            allPicked={allPicked}
            onSelect={(i, hero) => setHero('radiant', i, hero)}
            onSuggest={(i) => handleSuggest('radiant', i)}
            onClear={(i) => clearHero('radiant', i)}
            suggestions={suggestions}
            loading={loading}
            highlighted={mySide === 'radiant'}
          />
          <ChatBox onSend={handleChat} loading={chatLoading} />
          <DraftGrid
            side="dire"
            heroes={dire}
            allPicked={allPicked}
            onSelect={(i, hero) => setHero('dire', i, hero)}
            onSuggest={(i) => handleSuggest('dire', i)}
            onClear={(i) => clearHero('dire', i)}
            suggestions={suggestions}
            loading={loading}
            highlighted={mySide === 'dire'}
          />
        </div>
      </main>
    </div>
  )
}

export default App
