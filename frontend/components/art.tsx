/* Decorative background art. Pure SVG/CSS: no images to download, crisp at any size, and every
   animation is disabled by the prefers-reduced-motion rule in globals.css. All of it is aria-hidden. */

export function Aurora() {
  return (
    <div className="aurora" aria-hidden>
      <span />
      <span />
      <span />
    </div>
  );
}

// Supply-network nodes (x, y) in a 800 × 600 box — items, suppliers, departments.
const NODES: [number, number, number][] = [
  [90, 120, 0], [210, 70, 1], [330, 150, 2], [460, 90, 0], [600, 160, 1], [720, 80, 2],
  [150, 260, 1], [280, 300, 0], [420, 250, 2], [560, 320, 0], [690, 270, 1],
  [80, 430, 2], [230, 470, 1], [370, 420, 0], [510, 480, 2], [650, 430, 0], [760, 500, 1],
];
const EDGES: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4], [4, 5], [0, 6], [1, 6], [2, 7], [2, 8], [3, 8], [4, 9], [4, 10], [5, 10],
  [6, 7], [7, 8], [8, 9], [9, 10], [6, 11], [7, 12], [7, 13], [8, 13], [9, 14], [9, 15], [10, 15], [10, 16],
  [11, 12], [12, 13], [13, 14], [14, 15], [15, 16],
];

export function SupplyNetworkArt({ className, ecg = true }: { className?: string; ecg?: boolean }) {
  return (
    <svg viewBox="0 0 800 600" preserveAspectRatio="xMidYMid slice" className={className} aria-hidden>
      <defs>
        <radialGradient id="mf-node" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#5eead4" stopOpacity="1" />
          <stop offset="100%" stopColor="#5eead4" stopOpacity="0" />
        </radialGradient>
        <linearGradient id="mf-edge" x1="0" x2="1">
          <stop offset="0%" stopColor="#2dd4bf" stopOpacity="0.05" />
          <stop offset="50%" stopColor="#2dd4bf" stopOpacity="0.45" />
          <stop offset="100%" stopColor="#38bdf8" stopOpacity="0.05" />
        </linearGradient>
        <linearGradient id="mf-ecg" x1="0" x2="1">
          <stop offset="0%" stopColor="#5eead4" stopOpacity="0" />
          <stop offset="20%" stopColor="#5eead4" stopOpacity="0.9" />
          <stop offset="80%" stopColor="#7dd3fc" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#7dd3fc" stopOpacity="0" />
        </linearGradient>
        <filter id="mf-blur" x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur stdDeviation="3" />
        </filter>
      </defs>

      <g stroke="url(#mf-edge)" strokeWidth="1" fill="none">
        {EDGES.map(([a, b], i) => (
          <line key={i} x1={NODES[a][0]} y1={NODES[a][1]} x2={NODES[b][0]} y2={NODES[b][1]} />
        ))}
      </g>
      {/* packets flowing along a few edges */}
      <g stroke="#5eead4" strokeWidth="1.6" strokeLinecap="round" fill="none" opacity="0.8">
        {[0, 3, 8, 14, 19, 23, 27].map((e, i) => {
          const [a, b] = EDGES[e];
          return (
            <line
              key={e}
              className="flow-line"
              style={{ animationDelay: `${i * 0.35}s`, animationDuration: `${1.8 + (i % 3) * 0.6}s` }}
              x1={NODES[a][0]}
              y1={NODES[a][1]}
              x2={NODES[b][0]}
              y2={NODES[b][1]}
            />
          );
        })}
      </g>

      {NODES.map(([x, y, kind], i) => (
        <g key={i}>
          <circle cx={x} cy={y} r="14" fill="url(#mf-node)" opacity="0.35" />
          {kind === 0 ? (
            <rect x={x - 4} y={y - 4} width="8" height="8" rx="2" fill="#99f6e4" className="node-pulse" style={{ animationDelay: `${(i * 0.37) % 3}s` }} />
          ) : kind === 1 ? (
            <circle cx={x} cy={y} r="4" fill="#7dd3fc" className="node-pulse" style={{ animationDelay: `${(i * 0.51) % 3}s` }} />
          ) : (
            <path
              d={`M${x} ${y - 5} L${x + 4.5} ${y - 2.5} L${x + 4.5} ${y + 2.5} L${x} ${y + 5} L${x - 4.5} ${y + 2.5} L${x - 4.5} ${y - 2.5} Z`}
              fill="#c4b5fd"
              className="node-pulse"
              style={{ animationDelay: `${(i * 0.43) % 3}s` }}
            />
          )}
        </g>
      ))}

      {ecg && (
        <g>
          <path
            d="M0 360 L180 360 L205 360 L218 330 L232 395 L248 300 L262 385 L276 360 L420 360 L440 360 L452 338 L466 382 L480 318 L494 376 L508 360 L800 360"
            stroke="url(#mf-ecg)"
            strokeWidth="6"
            fill="none"
            filter="url(#mf-blur)"
            className="ecg-line"
            opacity="0.6"
          />
          <path
            d="M0 360 L180 360 L205 360 L218 330 L232 395 L248 300 L262 385 L276 360 L420 360 L440 360 L452 338 L466 382 L480 318 L494 376 L508 360 L800 360"
            stroke="url(#mf-ecg)"
            strokeWidth="2"
            fill="none"
            strokeLinejoin="round"
            className="ecg-line"
          />
        </g>
      )}
    </svg>
  );
}

/** Soft abstract waves for light banners. */
export function WaveArt({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 1200 240" preserveAspectRatio="none" className={className} aria-hidden>
      <defs>
        <linearGradient id="mf-wave-a" x1="0" x2="1">
          <stop offset="0%" stopColor="#5eead4" stopOpacity="0.0" />
          <stop offset="50%" stopColor="#5eead4" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#7dd3fc" stopOpacity="0.15" />
        </linearGradient>
      </defs>
      <path d="M0 170 C 200 110, 380 220, 600 160 S 1000 90, 1200 150 L1200 240 L0 240 Z" fill="url(#mf-wave-a)" />
      <path d="M0 200 C 240 150, 420 240, 660 190 S 1020 140, 1200 190 L1200 240 L0 240 Z" fill="#ffffff" opacity="0.06" />
      <path
        d="M0 130 C 220 80, 420 190, 640 130 S 1010 60, 1200 110"
        stroke="#99f6e4"
        strokeOpacity="0.5"
        strokeWidth="1.5"
        fill="none"
        className="flow-line"
      />
    </svg>
  );
}
