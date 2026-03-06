import { useState } from 'react'
import HeroSelector from './HeroSelector'

export default function DraftSlot({ label, hero, side, allPicked, onSelect, onSuggest, onClear, suggestions }) {
  const [open, setOpen] = useState(false)
  const isRadiant = side === 'radiant'

  const slotBg = hero
    ? isRadiant ? 'bg-green-500/8 border-green-500/20' : 'bg-red-500/8 border-red-500/20'
    : 'bg-white/[0.02] border-white/5 hover:border-white/10 hover:bg-white/[0.04]'

  return (
    <div className="relative">
      <div className="flex items-center gap-1.5">
        <button
          onClick={() => setOpen(!open)}
          className={`flex-1 flex items-center gap-3 px-4 py-2.5 rounded-lg border ${slotBg} transition-all cursor-pointer`}
        >
          <span className="text-[10px] font-medium text-gray-600 uppercase tracking-wider w-8 shrink-0">{label}</span>
          {hero ? (
            <span className="text-sm font-medium text-gray-100">{hero.displayName}</span>
          ) : (
            <span className="text-sm text-gray-600">Select hero</span>
          )}
        </button>

        {!hero && (
          <button
            onClick={onSuggest}
            className="px-2.5 py-2.5 text-[10px] font-medium uppercase tracking-wider text-gray-600 hover:text-gray-300 bg-white/[0.03] hover:bg-white/[0.06] border border-white/5 hover:border-white/10 rounded-lg transition-all cursor-pointer shrink-0"
          >
            Suggest
          </button>
        )}

        {hero && (
          <button
            onClick={() => onClear(side, hero)}
            className="px-2.5 py-2.5 text-gray-700 hover:text-gray-400 transition-colors cursor-pointer shrink-0"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" className="w-4 h-4">
              <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
            </svg>
          </button>
        )}
      </div>

      {open && (
        <HeroSelector
          allPicked={allPicked}
          onSelect={(h) => {
            onSelect(h)
            setOpen(false)
          }}
          onClose={() => setOpen(false)}
        />
      )}

      {suggestions && !hero && (
        <div className="mt-1.5 ml-12 flex flex-wrap gap-1">
          {suggestions.map((s) => (
            <button
              key={s.id}
              onClick={() => onSelect(s)}
              className="px-2.5 py-1 text-xs font-medium bg-indigo-500/10 hover:bg-indigo-500/20 border border-indigo-500/20 hover:border-indigo-500/30 rounded-md text-indigo-300 transition-all cursor-pointer"
            >
              {s.displayName}
              <span className="ml-1 text-indigo-500">{s.score > 0 ? '+' : ''}{s.score}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
