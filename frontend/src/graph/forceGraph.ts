// Canvas force-directed dependency graph, ported from the v11 prototype (pg* routines).
// Arrows read "A → B: A depends on B". Line style follows the backend link status.
import type { LinkStatus } from '../api/types'
import { VIGILANCE_COLOR, type Vigilance } from '../lib/model'

export interface FGNode {
  id: string
  name: string
  typeLabel: string
  isPos: boolean
  pin: boolean
  vig: Vigilance | 'pos'
  depth: number
  unresolved: boolean
}
export interface FGEdge { s: string; t: string; status: LinkStatus; required: boolean }

interface SimNode extends FGNode {
  x: number; y: number; vx: number; vy: number; fx: number | null; fy: number | null
  r: number; c: string; g: number; a: number; deg: number
}
interface SimLink { e: FGEdge; s: SimNode; t: SimNode; h: number }

const STATUS_STYLE: Record<LinkStatus, { rgb: string; dash: (k: number) => number[] }> = {
  CONFIRMED: { rgb: '163,180,202', dash: () => [] },
  OBSERVED: { rgb: '157,180,214', dash: (k) => [6 / k, 4 / k] },
  HYPOTHESIS: { rgb: '163,180,202', dash: (k) => [9 / k, 5 / k] },
  UNRESOLVED: { rgb: '242,180,91', dash: (k) => [1.5 / k, 4 / k] },
}

const hx = (c: string, a: number) => {
  const n = parseInt(c.slice(1), 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`
}

export class ForceGraph {
  private cv: HTMLCanvasElement
  private nodes: SimNode[] = []
  private links: SimLink[] = []
  private byId = new Map<string, SimNode>()
  private adj = new Map<string, Set<string>>()
  private posFocus = new Map<string, Set<string>>()
  private all = false
  private alpha = 1
  private rep = 4200
  private len = 130
  private view = { x: 0, y: 0, k: 1 }
  private target: { x: number; y: number; k: number } | null = null
  private fitted = false
  private W = 1
  private H = 1
  private raf = 0
  private hover: string | null = null
  private selected: string | null = null
  private path: string[] | null = null
  private drag: { o: SimNode | null; x: number; y: number; x0: number; y0: number; m: boolean } | null = null
  private onSelect: (id: string | null) => void
  private reduce: boolean
  private cleanup: (() => void)[] = []

  constructor(cv: HTMLCanvasElement, onSelect: (id: string | null) => void) {
    this.cv = cv
    this.onSelect = onSelect
    this.reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false
    this.bind()
    const loop = (ms: number) => {
      this.raf = requestAnimationFrame(loop)
      this.draw(ms / 1000)
    }
    this.raf = requestAnimationFrame(loop)
  }

  destroy(): void {
    cancelAnimationFrame(this.raf)
    this.cleanup.forEach((f) => f())
  }

  setData(nodes: FGNode[], edges: FGEdge[], all: boolean, posFocus: Map<string, Set<string>>): void {
    this.all = all
    this.posFocus = posFocus
    const deg = new Map<string, number>()
    nodes.forEach((n) => deg.set(n.id, 0))
    const valid = edges.filter((e) => deg.has(e.s) && deg.has(e.t))
    valid.forEach((e) => { deg.set(e.s, deg.get(e.s)! + 1); deg.set(e.t, deg.get(e.t)! + 1) })
    let sd = all ? 7 : 5
    const rnd = () => { sd = (sd * 16807) % 2147483647; return sd / 2147483647 }
    const pins = nodes.filter((n) => n.pin)
    const RAD = Math.max(260, pins.length * 55)
    this.nodes = nodes.map((n) => {
      const d = deg.get(n.id) ?? 0
      let x = 0
      let y = 0
      if (all) {
        const i = pins.indexOf(n)
        const a = n.pin ? (i / Math.max(1, pins.length)) * 6.283 : rnd() * 6.283
        const rad = n.pin ? RAD : rnd() * RAD * 0.8
        x = Math.cos(a) * rad
        y = Math.sin(a) * rad
      } else if (!n.pin) {
        const a = rnd() * 6.283
        const rad = n.depth * 150 + rnd() * 40
        x = Math.cos(a) * rad
        y = Math.sin(a) * rad
      }
      const r = all ? (n.isPos ? 7 : 2.6 + Math.sqrt(d) * 0.9) : n.pin ? 24 : 8 + Math.sqrt(d) * 2.2
      return {
        ...n, x, y, vx: 0, vy: 0, fx: !all && n.pin ? 0 : null, fy: !all && n.pin ? 0 : null,
        r, c: VIGILANCE_COLOR[n.vig], g: 0, a: 1, deg: d,
      }
    })
    this.byId = new Map(this.nodes.map((n) => [n.id, n]))
    this.links = valid.map((e) => ({ e, s: this.byId.get(e.s)!, t: this.byId.get(e.t)!, h: 0 }))
    this.adj = new Map(this.nodes.map((n) => [n.id, new Set<string>()]))
    this.links.forEach((l) => { this.adj.get(l.s.id)!.add(l.t.id); this.adj.get(l.t.id)!.add(l.s.id) })
    this.rep = all ? 3400 : 4200
    this.len = all ? 115 : 130
    this.alpha = 1
    for (let i = 0; i < (all ? 700 : 320); i++) this.tick()
    this.fitted = false
  }

  setSelected(id: string | null): void { this.selected = id }
  setPath(path: string[] | null): void { this.path = path }

  zoom(f: number): void {
    const v = this.view
    const k = Math.min(4, Math.max(0.2, v.k * f))
    const cx = this.W / 2
    const cy = this.H / 2
    this.target = { k, x: cx - ((cx - v.x) / v.k) * k, y: cy - ((cy - v.y) / v.k) * k }
  }

  fit(now = false): void {
    if (!this.nodes.length) return
    let mx = 60
    let my = 60
    this.nodes.forEach((o) => { mx = Math.max(mx, Math.abs(o.x) + o.r); my = Math.max(my, Math.abs(o.y) + o.r) })
    // Margins leave room for labels under the nodes and for the legend.
    const k = Math.min(1.8, Math.min(this.W / (2 * mx + 220), this.H / (2 * my + 200)))
    const t = { k, x: this.W / 2, y: this.H / 2 }
    if (now || this.reduce) this.view = t
    else this.target = t
  }

  focusOn(id: string): void {
    const o = this.byId.get(id)
    if (!o) return
    const k = Math.max(this.view.k, 1)
    this.target = { k, x: this.W / 2 - o.x * k, y: this.H / 2 - o.y * k }
  }

  private tick(): void {
    const a = this.alpha
    const N = this.nodes
    if (!N.length || a < 0.003) return
    for (let i = 0; i < N.length; i++) for (let j = i + 1; j < N.length; j++) {
      const p = N[i], q = N[j]
      let dx = q.x - p.x, dy = q.y - p.y, d2 = dx * dx + dy * dy
      if (d2 < 1) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 1 }
      const f = (this.rep * a) / d2
      p.vx -= dx * f; p.vy -= dy * f; q.vx += dx * f; q.vy += dy * f
    }
    for (const l of this.links) {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.hypot(dx, dy) || 1
      const f = ((d - this.len) / d) * 0.04 * a
      l.s.vx += dx * f; l.s.vy += dy * f; l.t.vx -= dx * f; l.t.vy -= dy * f
    }
    for (const o of N) {
      if (this.all) { o.vx -= o.x * 0.006 * a; o.vy -= o.y * 0.006 * a }
      if (!this.all && o.depth) {
        const dd = Math.hypot(o.x, o.y) || 1
        const f = ((dd - o.depth * 160) / dd) * 0.06 * a
        o.vx -= o.x * f; o.vy -= o.y * f
      }
      if (o.fx !== null && o.fy !== null) { o.x = o.fx; o.y = o.fy; o.vx = o.vy = 0; continue }
      o.vx *= 0.6; o.vy *= 0.6; o.x += o.vx; o.y += o.vy
    }
    this.alpha *= 0.985
  }

  private pt(e: PointerEvent | WheelEvent): [number, number] {
    const b = this.cv.getBoundingClientRect()
    return [e.clientX - b.left, e.clientY - b.top]
  }
  private toWorld(x: number, y: number): [number, number] { return [(x - this.view.x) / this.view.k, (y - this.view.y) / this.view.k] }
  private hit(x: number, y: number): SimNode | null {
    const [wx, wy] = this.toWorld(x, y)
    let best: SimNode | null = null
    let bd = 1e9
    for (const o of this.nodes) {
      const d = Math.hypot(o.x - wx, o.y - wy)
      const rr = this.all ? o.r / Math.min(1, this.view.k) : o.r
      if (d < rr + 8 / this.view.k && d < bd) { bd = d; best = o }
    }
    return best
  }

  private bind(): void {
    const cv = this.cv
    const down = (e: PointerEvent) => {
      const [x, y] = this.pt(e)
      cv.setPointerCapture(e.pointerId)
      this.drag = { o: this.hit(x, y), x, y, x0: x, y0: y, m: false }
      this.target = null
    }
    const move = (e: PointerEvent) => {
      const [x, y] = this.pt(e)
      const d = this.drag
      if (d) {
        if (Math.hypot(x - d.x0, y - d.y0) > 3) d.m = true
        if (d.o && !d.o.pin) {
          const [wx, wy] = this.toWorld(x, y)
          d.o.fx = wx; d.o.fy = wy
          this.alpha = Math.max(this.alpha, 0.3)
        } else {
          this.view.x += x - d.x; this.view.y += y - d.y
        }
        d.x = x; d.y = y
        return
      }
      const o = this.hit(x, y)
      this.hover = o ? o.id : null
      cv.style.cursor = o ? 'pointer' : 'grab'
    }
    const up = () => {
      const d = this.drag
      this.drag = null
      if (!d) return
      if (d.o && !d.o.pin) { d.o.fx = null; d.o.fy = null }
      if (!d.m) this.onSelect(d.o ? d.o.id : null)
    }
    const leave = () => { this.hover = null }
    const wheel = (e: WheelEvent) => {
      e.preventDefault()
      e.stopPropagation()
      this.target = null
      const [x, y] = this.pt(e)
      const [wx, wy] = this.toWorld(x, y)
      const k = Math.min(4, Math.max(0.2, this.view.k * Math.exp(-e.deltaY * 0.0015)))
      this.view = { k, x: x - wx * k, y: y - wy * k }
    }
    cv.addEventListener('pointerdown', down)
    cv.addEventListener('pointermove', move)
    cv.addEventListener('pointerup', up)
    cv.addEventListener('pointercancel', up)
    cv.addEventListener('pointerleave', leave)
    cv.addEventListener('wheel', wheel, { passive: false })
    this.cleanup.push(() => {
      cv.removeEventListener('pointerdown', down)
      cv.removeEventListener('pointermove', move)
      cv.removeEventListener('pointerup', up)
      cv.removeEventListener('pointercancel', up)
      cv.removeEventListener('pointerleave', leave)
      cv.removeEventListener('wheel', wheel)
    })
  }

  private focusSet(fid: string | null): Set<string> | null {
    if (this.path && this.path.length > 1) return new Set(this.path)
    if (!fid) return null
    if (this.all && this.posFocus.has(fid)) return this.posFocus.get(fid)!
    const adj = this.adj.get(fid)
    return adj ? new Set([fid, ...adj]) : null
  }

  private linkOn(l: SimLink, F: Set<string> | null, fid: string | null): boolean {
    if (!F) return false
    if (this.path && this.path.length > 1) {
      for (let i = 0; i < this.path.length - 1; i++) {
        const a = this.path[i], b = this.path[i + 1]
        if ((l.s.id === a && l.t.id === b) || (l.s.id === b && l.t.id === a)) return true
      }
      return false
    }
    if (this.all && fid && this.posFocus.has(fid)) return F.has(l.s.id) && F.has(l.t.id)
    return l.s.id === fid || l.t.id === fid
  }

  private draw(t: number): void {
    const cv = this.cv
    const b = cv.getBoundingClientRect()
    if (b.width < 10) return
    const dp = Math.min(window.devicePixelRatio || 1, 2)
    if (cv.width !== Math.round(b.width * dp) || cv.height !== Math.round(b.height * dp)) {
      cv.width = Math.round(b.width * dp)
      cv.height = Math.round(b.height * dp)
    }
    this.W = b.width
    this.H = b.height
    if (!this.fitted && this.nodes.length) { this.fit(true); this.fitted = true }
    this.tick()
    if (this.target) {
      const c = this.target, v = this.view
      v.k += (c.k - v.k) * 0.12; v.x += (c.x - v.x) * 0.12; v.y += (c.y - v.y) * 0.12
      if (Math.abs(c.x - v.x) < 0.5 && Math.abs(c.k - v.k) < 0.002) this.target = null
    }
    const ctx = cv.getContext('2d')!
    const { x: ox, y: oy, k } = this.view
    ctx.setTransform(dp, 0, 0, dp, 0, 0)
    ctx.clearRect(0, 0, b.width, b.height)
    const fid = this.hover ?? this.selected
    const F = this.focusSet(fid)
    const dimTo = this.all ? 0.07 : 0.18
    for (const o of this.nodes) {
      const on = !!F && F.has(o.id)
      o.g += ((o.id === fid ? 1 : on ? 0.6 : 0) - o.g) * 0.14
      o.a += ((!F || on ? 1 : dimTo) - o.a) * 0.14
    }
    if (this.all) this.renderAll(ctx, dp, ox, oy, k, F, fid)
    else this.renderSingle(ctx, dp, ox, oy, k, F, fid, t)
  }

  private renderSingle(ctx: CanvasRenderingContext2D, dp: number, ox: number, oy: number, k: number, F: Set<string> | null, fid: string | null, t: number): void {
    ctx.strokeStyle = 'rgba(163,180,202,0.06)'
    ctx.lineWidth = 1
    for (const d of [1, 2, 3, 4]) { ctx.beginPath(); ctx.arc(ox, oy, d * 160 * k, 0, 7); ctx.stroke() }
    ctx.setTransform(dp * k, 0, 0, dp * k, dp * ox, dp * oy)
    for (const l of this.links) {
      const on = this.linkOn(l, F, fid)
      l.h += ((on ? 1 : 0) - l.h) * 0.14
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.hypot(dx, dy) || 1, ux = dx / d, uy = dy / d
      const sx = l.s.x + ux * l.s.r, sy = l.s.y + uy * l.s.r, ex = l.t.x - ux * (l.t.r + 4), ey = l.t.y - uy * (l.t.r + 4)
      const st = STATUS_STYLE[l.e.status]
      const base = (F ? 0.08 : l.e.required ? 0.36 : 0.2) + l.h * 0.6
      const col = l.h > 0.05 ? `rgba(236,210,155,${base})` : `rgba(${st.rgb},${base})`
      ctx.strokeStyle = col
      ctx.fillStyle = col
      ctx.lineWidth = ((l.e.required ? 1 : 0.7) + l.h * 1.2) / k
      ctx.setLineDash(st.dash(k))
      ctx.beginPath(); ctx.moveTo(sx, sy); ctx.lineTo(ex, ey); ctx.stroke()
      ctx.setLineDash([])
      const A = Math.max(6, 7 / k)
      ctx.beginPath(); ctx.moveTo(ex, ey)
      ctx.lineTo(ex - ux * A - uy * A * 0.5, ey - uy * A + ux * A * 0.5)
      ctx.lineTo(ex - ux * A + uy * A * 0.5, ey - uy * A - ux * A * 0.5)
      ctx.closePath(); ctx.fill()
      if (l.h > 0.1 && !this.reduce) {
        const f = (t * 0.5) % 1
        ctx.fillStyle = `rgba(255,232,185,${l.h})`
        ctx.beginPath(); ctx.arc(sx + (ex - sx) * f, sy + (ey - sy) * f, 2.2 / k, 0, 7); ctx.fill()
      }
    }
    ctx.globalCompositeOperation = 'lighter'
    for (const o of this.nodes) {
      const gl = (o.pin ? 0.35 : 0.12) + o.g * 0.6, R = o.r * (2.2 + o.g * 2.6)
      const gr = ctx.createRadialGradient(o.x, o.y, 0, o.x, o.y, R)
      gr.addColorStop(0, hx(o.c, gl * o.a)); gr.addColorStop(1, hx(o.c, 0))
      ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(o.x, o.y, R, 0, 7); ctx.fill()
    }
    ctx.globalCompositeOperation = 'source-over'
    for (const o of this.nodes) {
      ctx.globalAlpha = o.a
      if (o.pin) {
        ctx.fillStyle = o.c; ctx.beginPath(); ctx.arc(o.x, o.y, o.r, 0, 7); ctx.fill()
        ctx.strokeStyle = hx(o.c, 0.6); ctx.lineWidth = 1.2 / k
        ctx.beginPath(); ctx.arc(o.x, o.y, o.r + 6 + (this.reduce ? 0 : Math.sin(t * 1.6)), 0, 7); ctx.stroke()
      } else {
        ctx.fillStyle = '#071629'; ctx.beginPath(); ctx.arc(o.x, o.y, o.r, 0, 7); ctx.fill()
        ctx.fillStyle = hx(o.c, 0.22); ctx.fill()
        ctx.strokeStyle = o.c; ctx.lineWidth = 1.8 / k
        if (o.unresolved) ctx.setLineDash([2 / k, 3 / k])
        ctx.stroke(); ctx.setLineDash([])
        ctx.fillStyle = o.c; ctx.beginPath(); ctx.arc(o.x, o.y, o.r * 0.38, 0, 7); ctx.fill()
      }
      if (o.id === this.selected) {
        ctx.strokeStyle = '#F2F5F9'; ctx.lineWidth = 1.5 / k
        ctx.beginPath(); ctx.arc(o.x, o.y, o.r + 8 + (this.reduce ? 0 : Math.sin(t * 2.4)), 0, 7); ctx.stroke()
      }
    }
    ctx.globalAlpha = 1
    ctx.setTransform(dp, 0, 0, dp, 0, 0)
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    const boxes: [number, number, number][] = []
    const order = [...this.nodes].sort((a, b) => (b.pin ? 9 : 0) + (b.id === fid ? 5 : 0) + b.r / 100 - ((a.pin ? 9 : 0) + (a.id === fid ? 5 : 0) + a.r / 100))
    for (const o of order) {
      const on = !!F && F.has(o.id)
      const lx = o.x * k + ox, ly = o.y * k + oy + o.r * k + (o.pin ? 12 : 7)
      const lw = o.name.length * (o.pin ? 8 : 6.4) + 8
      if (boxes.some((q) => Math.abs(q[0] - lx) < (q[2] + lw) / 2 && Math.abs(q[1] - ly) < 28)) continue
      boxes.push([lx, ly, lw])
      const la = on || !F ? 1 : 0.15
      ctx.shadowColor = 'rgba(2,8,16,0.95)'; ctx.shadowBlur = 6
      ctx.font = o.pin ? '400 19px "Cormorant Garamond",serif' : o.id === fid ? '500 12.5px Geist,sans-serif' : '400 12px Geist,sans-serif'
      ctx.fillStyle = `rgba(231,237,245,${la})`
      ctx.fillText(o.name, lx, ly)
      if (!o.pin) {
        ctx.font = '500 8.5px "Geist Mono",monospace'
        ctx.fillStyle = `rgba(138,155,179,${la * 0.9})`
        ctx.fillText(o.typeLabel.toUpperCase(), lx, ly + 15)
      }
      ctx.shadowBlur = 0
    }
  }

  private renderAll(ctx: CanvasRenderingContext2D, dp: number, ox: number, oy: number, k: number, F: Set<string> | null, fid: string | null): void {
    ctx.setTransform(dp * k, 0, 0, dp * k, dp * ox, dp * oy)
    ctx.lineCap = 'round'
    for (const l of this.links) {
      const on = this.linkOn(l, F, fid)
      l.h += ((on ? 1 : 0) - l.h) * 0.16
      const al = F ? 0.04 + l.h * 0.85 : 0.22
      ctx.strokeStyle = l.h > 0.05 ? `rgba(212,180,122,${al})` : `rgba(163,180,202,${al})`
      ctx.lineWidth = (0.7 + l.h * 0.9) / k
      ctx.setLineDash(l.e.status === 'UNRESOLVED' ? [3 / k, 3 / k] : [])
      ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke()
    }
    ctx.setLineDash([])
    for (const o of this.nodes) {
      ctx.globalAlpha = Math.max(0.07, o.a)
      const hi = o.id === fid || o.id === this.selected
      ctx.fillStyle = o.isPos ? '#D4B47A' : F && o.a > 0.5 ? o.c : '#B9C4D3'
      const rr = (o.r * (hi ? 1.35 : 1)) / Math.min(1, k)
      ctx.beginPath(); ctx.arc(o.x, o.y, rr, 0, 7); ctx.fill()
      if (hi) {
        ctx.strokeStyle = '#F2F5F9'; ctx.lineWidth = 1.4 / k
        ctx.beginPath(); ctx.arc(o.x, o.y, rr + 4 / k, 0, 7); ctx.stroke()
      }
    }
    ctx.globalAlpha = 1
    ctx.setTransform(dp, 0, 0, dp, 0, 0)
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    const boxes: [number, number, number][] = []
    const score = (o: SimNode) => (o.id === fid ? 20 : 0) + (F && F.has(o.id) ? 10 : 0) + (o.isPos ? 5 : 0) + o.deg / 50
    const order = [...this.nodes].sort((a, b) => score(b) - score(a))
    for (const o of order) {
      const inF = !!F && F.has(o.id)
      const show = o.isPos || inF || k > 0.9 || (o.deg >= 3 && k > 0.4)
      if (!show) continue
      const fs = o.isPos ? 12 : 10.5
      ctx.font = `${o.isPos ? '500 ' : '400 '}${fs}px Geist, system-ui, sans-serif`
      const lw = ctx.measureText(o.name).width + 6
      const lx = o.x * k + ox, ly = o.y * k + oy + o.r * Math.max(1, k) * 1.35 + 6
      if (boxes.some((q) => Math.abs(q[0] - lx) < (q[2] + lw) / 2 && Math.abs(q[1] - ly) < fs + 4)) continue
      boxes.push([lx, ly, lw])
      const la = !F ? (o.isPos ? 0.95 : 0.6) : inF ? 1 : 0.08
      ctx.lineJoin = 'round'; ctx.lineWidth = 3.5
      ctx.strokeStyle = `rgba(4,16,31,${Math.min(1, la * 1.1)})`
      ctx.strokeText(o.name, lx, ly)
      ctx.fillStyle = o.isPos ? `rgba(230,207,160,${la})` : `rgba(220,228,238,${la})`
      ctx.fillText(o.name, lx, ly)
    }
  }
}
