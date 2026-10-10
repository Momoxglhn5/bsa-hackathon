import { ReactFlow, ReactFlowProvider } from '@xyflow/react'
import type { PathStep } from '../lib/graph'
import DependencyEdge from './DependencyEdge'
import type { DependencyFlowEdge } from './DependencyEdge'
import RadarNode from './RadarNode'
import type { RadarFlowNode } from './RadarNode'

export type StepState = 'shocked' | 'impaired' | 'unchanged'

export const STEP_COLOR: Record<StepState, string> = {
  shocked: '#ef4444',
  impaired: '#f59e0b',
  unchanged: '#475569',
}

const nodeTypes = { radar: RadarNode }
const edgeTypes = { dependency: DependencyEdge }
const GAP = 200
const R = 9

/** Read-only React Flow strip: the shock travelling left to right toward the position. */
export default function PathFlow({ steps, states }: { steps: PathStep[]; states: StepState[] }) {
  const nodes: RadarFlowNode[] = steps.map((s, i) => {
    const r = s.node.kind === 'position' ? 12 : R
    return {
      id: s.node.id,
      type: 'radar',
      position: { x: i * GAP - r, y: -r },
      width: r * 2,
      height: r * 2,
      draggable: false,
      selectable: false,
      focusable: false,
      data: {
        node: s.node,
        radius: r,
        color: STEP_COLOR[states[i] ?? 'unchanged'],
        scale: 1,
        opacity: 1,
        showLabel: true,
        emphasis: states[i] === 'shocked' ? 'hover' : 'none',
      },
    }
  })
  const edges: DependencyFlowEdge[] = steps.slice(0, -1).map((s, i) => {
    const next = steps[i + 1]!.node
    return {
      id: `${s.node.id}->${next.id}`,
      source: s.node.id,
      target: next.id,
      type: 'dependency',
      selectable: false,
      focusable: false,
      data: { edge: s.via!, look: 'out', targetRadius: next.kind === 'position' ? 12 : R },
    }
  })
  return (
    // Keyed on the path: fitView only runs on mount, and each scenario has its own path.
    <ReactFlowProvider key={steps.map((s) => s.node.id).join('|')}>
      <div className="h-[130px] w-full" aria-hidden="true">
        <ReactFlow<RadarFlowNode, DependencyFlowEdge>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.2, maxZoom: 1.2 }}
          panOnDrag={false}
          zoomOnScroll={false}
          zoomOnPinch={false}
          zoomOnDoubleClick={false}
          preventScrolling={false}
          nodesConnectable={false}
          proOptions={{ hideAttribution: true }}
        />
      </div>
    </ReactFlowProvider>
  )
}
