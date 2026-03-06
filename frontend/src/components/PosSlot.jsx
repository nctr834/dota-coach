export default function PosSlot({ roles, selectedPos, onSelect }) {
    return (
        <div className="inline-flex rounded-lg border border-white/10 bg-white/[0.03] p-1 gap-0.5">
            {roles.map((role, i) => {
                const active = selectedPos === role
                return (
                    <button
                        key={i}
                        onClick={() => onSelect(role)}
                        className={`px-4 py-2 text-xs font-medium rounded-md transition-all cursor-pointer ${active
                            ? 'bg-white/10 text-white shadow-sm'
                            : 'text-gray-500 hover:text-gray-300 hover:bg-white/5'
                            }`}
                    >
                        Pos {role}
                    </button>
                )
            })}
        </div>
    )
}
