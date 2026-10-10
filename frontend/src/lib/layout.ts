import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation } from 'd3-force'
import type { SimulationLinkDatum, SimulationNodeDatum } from 'd3-force'
import type { Graph } from './graph'

interface SimNode extends SimulationNodeDatum {
  id: string
  r: number
}

export function nodeRadius(kind: string, degree: number): number {
  return kind === 'position' ? 11 : 5 + Math.sqrt(degree) * 3
}

/**
 * Static force layout, computed once outside React. Seeded on a phyllotaxis spiral so
 * the same graph always lands in the same place (no Math.random).
 */
export function forceLayout(graph: Graph, ticks = 400): Map<string, { x: number; y: number }> {
  const nodes: SimNode[] = graph.nodes.map((n, i) => {
    const radius = 30 * Math.sqrt(i + 0.5)
    const angle = i * Math.PI * (3 - Math.sqrt(5))
    return { id: n.id, r: nodeRadius(n.kind, n.degree), x: radius * Math.cos(angle), y: radius * Math.sin(angle) }
  })
  const links: SimulationLinkDatum<SimNode>[] = graph.edges.map((e) => ({ source: e.source, target: e.target }))
  const sim = forceSimulation(nodes)
    .force(
      'link',
      forceLink<SimNode, SimulationLinkDatum<SimNode>>(links)
        .id((d) => d.id)
        .distance(110)
        .strength(0.6),
    )
    .force('charge', forceManyBody().strength(-420))
    .force('collide', forceCollide<SimNode>().radius((d) => d.r + 28))
    .force('center', forceCenter(0, 0))
    .stop()
  sim.tick(ticks)
  return new Map(nodes.map((n) => [n.id, { x: n.x ?? 0, y: n.y ?? 0 }]))
}
