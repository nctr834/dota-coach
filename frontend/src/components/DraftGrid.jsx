import DraftSlot from './DraftSlot'

const LABELS = ['Pos 1', 'Pos 2', 'Pos 3', 'Pos 4', 'Pos 5']

export default function DraftGrid({ side, heroes, allPicked, onSelect, onSuggest, onClear, suggestions, loading, highlighted }) {
  const isRadiant = side === 'radiant'
  const title = isRadiant ? 'Radiant' : 'Dire'
  const accentColor = isRadiant ? 'from-green-500/40' : 'from-red-500/40'
  const titleColor = isRadiant ? 'text-green-400' : 'text-red-400'
  const dotColor = isRadiant ? 'bg-green-500' : 'bg-red-500'
  const highlightBorder = highlighted
    ? isRadiant ? 'border-green-500/30' : 'border-red-500/30'
    : 'border-white/10'

  return (
    <div className={`relative rounded-xl border ${highlightBorder} bg-white/[0.02] overflow-hidden transition-colors`}>
      {/* Top accent line */}
      <div className={`absolute inset-x-0 top-0 h-px bg-gradient-to-r ${accentColor} to-transparent`} />

      <div className="px-5 pt-5 pb-2 flex items-center gap-2">
        <span className={`w-2 h-2 rounded-full ${dotColor}`} />
        <h2 className={`text-sm font-semibold uppercase tracking-wider ${titleColor}`}>
          {title}
        </h2>
        <span className="text-xs text-gray-600 ml-auto flex items-center gap-2">
          {highlighted && <span className="text-[10px] font-medium text-gray-500 bg-white/5 px-1.5 py-0.5 rounded">You</span>}
          {heroes.filter(Boolean).length}/5
        </span>
      </div>

      <div className="px-3 pb-4 space-y-1">
        {heroes.map((hero, i) => (
          <DraftSlot
            key={i}
            label={LABELS[i]}
            hero={hero}
            side={side}
            allPicked={allPicked}
            onSelect={(h) => onSelect(i, h)}
            onSuggest={() => onSuggest(i)}
            onClear={() => onClear(i)}
            suggestions={suggestions[`${side}-${i}`] || null}
            loading={loading}
          />
        ))}
      </div>
    </div>
  )
}
