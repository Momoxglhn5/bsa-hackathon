import { BaseEdge } from '@xyflow/react'
import type { Edge, EdgeProps } from '@xyflow/react'
import type { GraphEdge } from '../lib/graph'

export type EdgeLook = 'idle' | 'out' | 'in' | 'upstream' | 'context' | 'faded' | 'selected'

export interface DependencyEdgeData extends Record<string, unknown> {
  edge: GraphEdge
  look: EdgeLook
  /** Rendered radius of the target node, so the arrow stops at its rim. */
  targetRadius: number
}

export type DependencyFlowEdge = Edge<DependencyEdgeData, 'dependency'>

const LOOK: Record<EdgeLook, { stroke: string; width: number; opacity: number; arrow: boolean; className?: string; dash?: string }> = {
  idle: { stroke: '#94a3b8', width: 1, opacity: 0.34, arrow: false },
  out: { stroke: '#818cf8', width: 1.7, opacity: 1, arrow: true, className: 'edge-flow' },
  in: { stroke: '#e2e8f0', width: 1.4, opacity: 1, arrow: true },
  upstream: { stroke: '#818cf8', width: 1.2, opacity: 0.6, arrow: false, dash: '3 4' },
  context: { stroke: '#94a3b8', width: 1, opacity: 0.3, arrow: false },
  faded: { stroke: '#94a3b8', width: 1, opacity: 0.05, arrow: false },
  selected: { stroke: '#f3f4f6', width: 2, opacity: 1, arrow: true, className: 'edge-flow' },
}

export default function DependencyEdge({ id, sourceX, sourceY, targetX, targetY, data }: EdgeProps<DependencyFlowEdge>) {
  if (!data) return null
  const look = LOOK[data.look]
  const dx = targetX - sourceX
  const dy = targetY - sourceY
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const tipX = targetX - ux * (data.targetRadius + 2)
  const tipY = targetY - uy * (data.targetRadius + 2)
  const path = `M ${sourceX},${sourceY} L ${tipX},${tipY}`
  const ah = 7
  const aw = 3.5
  const arrow = `M ${tipX},${tipY} L ${tipX - ux * ah - uy * aw},${tipY - uy * ah + ux * aw} L ${tipX - ux * ah + uy * aw},${tipY - uy * ah - ux * aw} Z`
  return (
    <g className="radar-fade" style={{ opacity: look.opacity }}>
      <BaseEdge
        id={id}
        path={path}
        interactionWidth={12}
        className={look.className}
        style={{ stroke: look.stroke, strokeWidth: look.width, strokeDasharray: look.dash }}
      />
      {look.arrow && <path d={arrow} fill={look.stroke} />}
    </g>
  )
}
