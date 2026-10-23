import { useId, useRef, useState } from 'react'

import { niceTicks } from './ticks'

export interface LineSeries {
  key: string
  label: string
  /** One of the chart colour slots: 1 is the accent, 2 the second hue. */
  slot: 1 | 2
  values: number[]
}

interface LineChartProps {
  /** One label per point, the same length as every series (a date). */
  points: string[]
  /** How a point's label is written in the axis and the tooltip. */
  formatPoint: (point: string) => string
  series: LineSeries[]
  /** Names the chart for assistive technology. */
  label: string
  className?: string
}

const W = 960
const H = 260
const PAD = { top: 12, right: 16, bottom: 28, left: 36 }

const SLOT_VAR = { 1: 'var(--viz-1)', 2: 'var(--viz-2)' } as const

/**
 * Lines over time: 2px lines, an end dot ringed in the surface colour, hairline grid, a legend that is always there for two series
 * and the last value written beside each line, a crosshair with a tooltip on hover, and the same figures as a table underneath.
 */
export function LineChart({ points, formatPoint, series, label, className }: LineChartProps) {
  const titleId = useId()
  const svg = useRef<SVGSVGElement>(null)
  const [hover, setHover] = useState<number | null>(null)

  if (points.length < 2) return <p className="py-6 text-center text-sm text-muted-foreground">Not enough days to draw a line yet.</p>

  const max = Math.max(...series.flatMap((s) => s.values), 0)
  const ticks = niceTicks(max)
  const top = ticks[ticks.length - 1]
  const innerW = W - PAD.left - PAD.right
  const innerH = H - PAD.top - PAD.bottom
  const x = (i: number) => PAD.left + (i / (points.length - 1)) * innerW
  const y = (v: number) => PAD.top + innerH - (v / top) * innerH
  const every = Math.ceil(points.length / 6)

  function move(event: React.MouseEvent<SVGRectElement>) {
    const box = svg.current?.getBoundingClientRect()
    if (!box) return
    const px = ((event.clientX - box.left) / box.width) * W
    const index = Math.round(((px - PAD.left) / innerW) * (points.length - 1))
    setHover(Math.min(points.length - 1, Math.max(0, index)))
  }

  return (
    <figure className={className}>
      <figcaption id={titleId} className="sr-only">
        {label}
      </figcaption>
      <ul aria-label="Legend" className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">
        {series.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5">
            <span aria-hidden className="inline-block h-0.5 w-4 rounded" style={{ background: SLOT_VAR[s.slot] }} />
            {s.label}
          </li>
        ))}
      </ul>
      <div className="relative">
        <svg ref={svg} viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={titleId} className="h-auto w-full">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} stroke="var(--viz-grid)" strokeWidth={1} />
              <text x={PAD.left - 6} y={y(t) + 4} textAnchor="end" className="fill-muted-foreground text-[11px]">
                {t}
              </text>
            </g>
          ))}
          {points.map((p, i) =>
            i % every === 0 || i === points.length - 1 ? (
              <text key={p} x={x(i)} y={H - 8} textAnchor={i === 0 ? 'start' : i === points.length - 1 ? 'end' : 'middle'} className="fill-muted-foreground text-[11px]">
                {formatPoint(p)}
              </text>
            ) : null,
          )}
          {series.map((s) => (
            <polyline key={s.key} fill="none" stroke={SLOT_VAR[s.slot]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(' ')} />
          ))}
          {series.map((s) => {
            const last = s.values.length - 1
            return <circle key={s.key} cx={x(last)} cy={y(s.values[last])} r={4} fill={SLOT_VAR[s.slot]} stroke="var(--card)" strokeWidth={2} />
          })}
          {hover !== null && (
            <g pointerEvents="none">
              <line x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} stroke="var(--viz-crosshair)" strokeWidth={1} />
              {series.map((s) => (
                <circle key={s.key} cx={x(hover)} cy={y(s.values[hover])} r={4} fill={SLOT_VAR[s.slot]} stroke="var(--card)" strokeWidth={2} />
              ))}
            </g>
          )}
          <rect x={PAD.left} y={PAD.top} width={innerW} height={innerH} fill="transparent" onMouseMove={move} onMouseLeave={() => setHover(null)} />
        </svg>
        {hover !== null && (
          <div
            role="tooltip"
            className="pointer-events-none absolute top-0 z-10 rounded-md border bg-popover px-2.5 py-1.5 text-xs shadow-md"
            style={{ left: `${(x(hover) / W) * 100}%`, transform: hover > points.length / 2 ? 'translateX(calc(-100% - 10px))' : 'translateX(10px)' }}
          >
            <p className="font-medium">{formatPoint(points[hover])}</p>
            {series.map((s) => (
              <p key={s.key} className="flex items-center gap-1.5 text-muted-foreground">
                <span aria-hidden className="inline-block size-2 rounded-full" style={{ background: SLOT_VAR[s.slot] }} />
                {s.label}: <span className="font-medium text-foreground tabular-nums">{s.values[hover]}</span>
              </p>
            ))}
          </div>
        )}
      </div>
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted-foreground">Show as a table</summary>
        <div className="mt-2 max-h-56 overflow-auto">
          <table className="w-full text-left" aria-label={`${label}, as a table`}>
            <thead>
              <tr className="text-muted-foreground">
                <th className="py-1 font-normal">Day</th>
                {series.map((s) => (
                  <th key={s.key} className="py-1 text-right font-normal">
                    {s.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {points.map((p, i) => (
                <tr key={p} className="border-t">
                  <td className="py-1">{formatPoint(p)}</td>
                  {series.map((s) => (
                    <td key={s.key} className="py-1 text-right tabular-nums">
                      {s.values[i]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  )
}
