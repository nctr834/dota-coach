import { useState, useRef, useEffect } from 'react'
import heroes from '../data/hero_data.json'

export default function HeroSelector({ allPicked, onSelect, onClose }) {
  const [search, setSearch] = useState('')
  const inputRef = useRef(null)
  const panelRef = useRef(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    function handleClick(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [onClose])

  const pickedIds = new Set(allPicked.map(h => h.id))

  const heroesArray = Object.values(heroes)
  const filtered = heroesArray.filter(h =>
    h.displayName.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div
      ref={panelRef}
      className="absolute z-50 top-full left-0 right-0 mt-1.5 bg-[#111827] border border-white/10 rounded-xl shadow-2xl shadow-black/50 overflow-hidden"
    >
      <div className="p-2.5">
        <input
          ref={inputRef}
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search heroes..."
          className="w-full px-3 py-2 bg-white/5 border border-white/10 rounded-lg text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/25 transition-all"
        />
      </div>
      <ul className="hero-list max-h-56 overflow-y-auto px-1.5 pb-1.5">
        {filtered.map(hero => {
          const picked = pickedIds.has(hero.id)
          return (
            <li key={hero.id}>
              <button
                disabled={picked}
                onClick={() => onSelect(hero)}
                className={`w-full text-left px-3 py-1.5 text-sm rounded-md transition-colors cursor-pointer ${picked
                  ? 'text-gray-700 cursor-not-allowed'
                  : 'text-gray-300 hover:bg-white/5 hover:text-white'
                  }`}
              >
                {hero.displayName}
              </button>
            </li>
          )
        })}
        {filtered.length === 0 && (
          <li className="px-3 py-4 text-sm text-gray-600 text-center">No heroes found</li>
        )}
      </ul>
    </div>
  )
}
