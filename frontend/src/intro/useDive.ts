// Scroll-driven dive: 0 surface → 0.5 entrance gate → 1 dashboard. DOM layers are
// faded directly in the animation frame to avoid re-rendering React at 60 fps.
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { SeaScene, ss, type SceneGraph } from './scene'

export interface DiveRefs {
  mount: RefObject<HTMLDivElement | null>
  input: RefObject<HTMLDivElement | null>
  hero: RefObject<HTMLElement | null>
  cue: RefObject<HTMLDivElement | null>
  dive: RefObject<HTMLDivElement | null>
  gate: RefObject<HTMLDivElement | null>
  topSea: RefObject<HTMLDivElement | null>
  topDash: RefObject<HTMLDivElement | null>
  dash: RefObject<HTMLElement | null>
}

const reducedMotion = () => typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

export function useDive(refs: DiveRefs, graph: SceneGraph | null, covered: boolean) {
  const startInDash = typeof window !== 'undefined' && window.location.hash === '#dashboard'
  const pRef = useRef(startInDash ? 1 : 0)
  const target = useRef(startInDash ? 1 : 0)
  const entered = useRef(startInDash)
  const scene = useRef<SeaScene | null>(null)
  const [noWebgl, setNoWebgl] = useState(false)
  const [inDash, setInDash] = useState(startInDash)
  const inDashRef = useRef(startInDash)
  // An opaque page (graph) hides the scene entirely: skip WebGL work meanwhile.
  const coveredRef = useRef(covered)
  coveredRef.current = covered

  const enter = useCallback(() => {
    entered.current = true
    target.current = 1
    if (window.location.hash !== '#dashboard') history.replaceState(null, '', '#dashboard')
    window.setTimeout(() => { if (target.current === 1 && pRef.current < 0.9) pRef.current = 1 }, 1500)
  }, [])
  const toGate = useCallback(() => { target.current = 0.5 }, [])
  const surface = useCallback(() => {
    entered.current = false
    target.current = 0
    history.replaceState(null, '', window.location.pathname + window.location.search)
  }, [])

  // Scene lifecycle.
  useEffect(() => {
    const mount = refs.mount.current
    if (!mount) return
    let disposed = false
    const s = new SeaScene(mount)
    const timeout = new Promise<never>((_, rej) => setTimeout(() => rej(new Error('three timeout')), 12_000))
    Promise.race([s.init(), timeout]).then(() => {
      if (disposed) { s.dispose(); return }
      scene.current = s
    }).catch(() => { if (!disposed) setNoWebgl(true) })
    return () => { disposed = true; scene.current?.dispose(); scene.current = null }
  }, [refs.mount])

  useEffect(() => {
    if (!graph) return
    let raf = 0
    const apply = () => {
      if (scene.current) scene.current.setGraph(graph)
      else raf = requestAnimationFrame(apply)
    }
    apply()
    return () => cancelAnimationFrame(raf)
  }, [graph])

  // Input: wheel, keys and drag on the intro layer only; the dashboard keeps native scrolling.
  useEffect(() => {
    let snapT = 0
    const onWheel = (e: WheelEvent) => {
      const t = e.target as Element | null
      if (pRef.current > 0.95 && t?.closest?.('[data-scroll="1"]')) return
      if (pRef.current > 0.95 && e.deltaY > 0) return
      e.preventDefault()
      target.current = Math.min(1, Math.max(0, target.current + e.deltaY * 0.0011))
      window.clearTimeout(snapT)
      snapT = window.setTimeout(() => { if (target.current > 0.85) target.current = 1 }, 200)
    }
    const onKey = (e: KeyboardEvent) => {
      if (pRef.current > 0.95) return
      const el = e.target as HTMLElement | null
      if (el && el.closest('button, a, input, select, textarea, [role="dialog"]')) return
      if (['ArrowDown', 'PageDown', ' '].includes(e.key)) {
        e.preventDefault()
        target.current = target.current < 0.45 ? 0.5 : 1
      } else if (['ArrowUp', 'PageUp'].includes(e.key)) {
        e.preventDefault()
        target.current = 0
      }
    }
    window.addEventListener('wheel', onWheel, { passive: false })
    window.addEventListener('keydown', onKey)
    const el = refs.input.current
    let drag: { y: number } | null = null
    const down = (e: PointerEvent) => { el?.setPointerCapture(e.pointerId); drag = { y: e.clientY } }
    const move = (e: PointerEvent) => {
      if (!drag || pRef.current >= 0.95) return
      const dy = e.clientY - drag.y
      drag.y = e.clientY
      target.current = Math.min(1, Math.max(0, target.current - dy * 0.0016))
      if (target.current > 0.95) target.current = 1
    }
    const up = () => { drag = null }
    el?.addEventListener('pointerdown', down)
    el?.addEventListener('pointermove', move)
    el?.addEventListener('pointerup', up)
    el?.addEventListener('pointercancel', up)
    return () => {
      window.removeEventListener('wheel', onWheel)
      window.removeEventListener('keydown', onKey)
      el?.removeEventListener('pointerdown', down)
      el?.removeEventListener('pointermove', move)
      el?.removeEventListener('pointerup', up)
      el?.removeEventListener('pointercancel', up)
      window.clearTimeout(snapT)
    }
  }, [refs.input])

  // DOM layers follow p; applied once before paint so a deep link never waits for a frame.
  const applyLayers = useCallback((p: number) => {
    const { hero, cue, dive, gate, topSea, topDash, dash, input } = refs
    if (hero.current) {
      hero.current.style.opacity = String(1 - ss(0, 0.14, p))
      hero.current.style.transform = `translateY(${-p * 120}px)`
      hero.current.style.filter = `blur(${ss(0, 0.14, p) * 6}px)`
    }
    if (gate.current) {
      const gv = ss(0.4, 0.5, p) * (1 - ss(0.5, 0.62, p))
      gate.current.style.opacity = String(gv)
      gate.current.style.pointerEvents = gv > 0.5 ? 'auto' : 'none'
      gate.current.style.visibility = gv > 0.003 ? 'visible' : 'hidden'
    }
    if (cue.current) cue.current.style.opacity = String(1 - ss(0, 0.05, p))
    if (topSea.current) {
      topSea.current.style.opacity = String(1 - ss(0.4, 0.7, p))
      topSea.current.style.pointerEvents = p < 0.6 ? 'auto' : 'none'
    }
    if (dive.current) {
      dive.current.style.opacity = String(ss(0.62, 0.74, p) * (1 - ss(0.86, 0.95, p)))
      dive.current.style.transform = `translate(-50%, calc(-50% + ${(0.8 - p) * 50}px))`
    }
    const g = ss(0.94, 1, p)
    if (topDash.current) {
      topDash.current.style.opacity = String(g)
      topDash.current.style.pointerEvents = g > 0.5 ? 'auto' : 'none'
    }
    if (dash.current) {
      dash.current.style.opacity = String(g)
      dash.current.style.pointerEvents = g > 0.5 ? 'auto' : 'none'
      dash.current.style.transform = `translateY(${(1 - g) * 24}px)`
    }
    if (input.current) input.current.style.display = p > 0.97 ? 'none' : 'block'
    const nowDash = g > 0.5
    if (nowDash !== inDashRef.current) {
      inDashRef.current = nowDash
      setInDash(nowDash)
    }
  }, [refs])

  useLayoutEffect(() => { applyLayers(pRef.current) }, [applyLayers])

  // Frame loop.
  useEffect(() => {
    let raf = 0
    let last = performance.now() / 1000
    const reduce = reducedMotion()
    const loop = (ms: number) => {
      raf = requestAnimationFrame(loop)
      const t = ms / 1000
      const dt = Math.min(0.1, t - last)
      last = t
      if (target.current < 0.5) entered.current = false
      if (!entered.current && target.current > 0.5) target.current = 0.5
      pRef.current = reduce ? target.current : pRef.current + (target.current - pRef.current) * (1 - Math.exp(-dt * 3.2))
      if (Math.abs(target.current - pRef.current) < 0.0004) pRef.current = target.current
      const p = pRef.current
      if (!coveredRef.current) scene.current?.frame(t, dt, p, !reduce, p > 0.995)
      applyLayers(p)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [applyLayers])

  return { enter, toGate, surface, noWebgl, inDash }
}
