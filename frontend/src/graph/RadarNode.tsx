import { Handle, Position } from '@xyflow/react'
import type { Node, NodeProps } from '@xyflow/react'
import type { GraphNode } from '../lib/graph'

export interface RadarNodeData extends Record<string, unknown> {
  node: GraphNode
  radius: number
  color: string
  scale: number
  opacity: number
  showLabel: boolean
  emphasis: 'hover' | 'selected' | 'none'
}

export type RadarFlowNode = Node<RadarNodeData, 'radar'>

// Both handles sit at the centre: edges are drawn centre to centre, Obsidian-style.
const centre = { left: '50%', top: '50%', transform: 'translate(-50%, -50%)', width: 1, height: 1, minWidth: 0, minHeight: 0, border: 0, background: 'transparent', opacity: 0 }

export default function RadarNode({ data }: NodeProps<RadarFlowNode>) {
  const { node, radius, color, scale, opacity, showLabel, emphasis } = data
  const d = radius * 2
  const isPosition = node.kind === 'position'
  const shadow =
    emphasis === 'selected'
      ? '0 0 0 3px rgba(99,102,241,.6), 0 0 18px rgba(129,140,248,.55)'
      : emphasis === 'hover'
        ? `0 0 16px ${color}`
        : 'none'
  return (
    <div className="radar-fade relative" style={{ width: d, height: d, opacity }}>
      <Handle type="target" position={Position.Top} isConnectable={false} style={centre} />
      <Handle type="source" position={Position.Top} isConnectable={false} style={centre} />
      <div
        className="radar-node size-full rounded-full"
        style={{
          transform: `scale(${scale})`,
          background: isPosition ? '#0b1120' : color,
          border: isPosition ? `2px dashed ${color}` : emphasis === 'selected' ? '2px solid #fff' : 'none',
          boxShadow: shadow,
        }}
      />
      <span
        className="radar-fade pointer-events-none absolute left-1/2 -translate-x-1/2 text-[11px] whitespace-nowrap"
        style={{
          top: radius + radius * scale + 4,
          opacity: showLabel ? 1 : 0,
          color: emphasis === 'none' ? '#9aa6b8' : '#f3f4f6',
        }}
      >
        {node.label}
      </span>
    </div>
  )
}
