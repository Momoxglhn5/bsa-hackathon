// Intro ocean scene (three.js), ported from the v11 prototype. Purely decorative:
// the dependency constellation seen during the dive is built from the backend graph.
import type * as THREE_NS from 'three'

type THREE = typeof THREE_NS

const WAVES = [[1.0, 0.3, 0.11, 62], [0.4, 1.0, 0.09, 33], [-0.6, 0.8, 0.07, 19], [0.9, -0.4, 0.05, 10], [-0.2, 1.0, 0.04, 6]]
const UW = 'vec3(0.002,0.018,0.045)'
const FOGD = 0.0105
const SKY = `
uniform vec3 uSun;
vec3 sky(vec3 d){float y=max(d.y,0.0);vec3 zen=vec3(0.002,0.01,0.035);vec3 hor=vec3(0.07,0.16,0.33);vec3 c=mix(hor,zen,pow(y,0.42));float s=max(dot(d,uSun),0.0);c+=vec3(1.0,0.93,0.82)*pow(s,900.0)*14.0+vec3(0.7,0.8,1.0)*pow(s,160.0)*0.6+vec3(0.35,0.5,0.85)*pow(s,5.0)*0.12;if(d.y<0.0)c=hor*0.55;return c;}
vec3 tm(vec3 c){c=c/(1.0+c);return pow(c,vec3(1.0/2.2));}
`
const WATER_V = `
uniform float uTime;varying vec3 vW;varying vec3 vN;varying float vH;
const float PI=3.14159265;
vec3 g(vec4 w,vec3 p,inout vec3 t,inout vec3 b){float k=2.0*PI/w.w;float c=sqrt(9.8/k);vec2 d=normalize(w.xy);float f=k*(dot(d,p.xz)-c*uTime);float a=w.z/k;
t+=vec3(-d.x*d.x*(w.z*sin(f)),d.x*(w.z*cos(f)),-d.x*d.y*(w.z*sin(f)));b+=vec3(-d.x*d.y*(w.z*sin(f)),d.y*(w.z*cos(f)),-d.y*d.y*(w.z*sin(f)));return vec3(d.x*(a*cos(f)),a*sin(f),d.y*(a*cos(f)));}
void main(){vec3 p=(modelMatrix*vec4(position,1.0)).xyz;vec3 t=vec3(1,0,0),b=vec3(0,0,1);vec3 q=p;
${WAVES.map((w) => `q+=g(vec4(${w.map((x) => x.toFixed(3)).join(',')}),p,t,b);`).join('')}
vN=normalize(cross(b,t));vW=q;vH=q.y;gl_Position=projectionMatrix*viewMatrix*vec4(q,1.0);}`
const WATER_F = `
uniform float uTime;uniform float uGate;varying vec3 vW;varying vec3 vN;varying float vH;
${SKY}
float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.0-2.0*f);return mix(mix(h(i),h(i+vec2(1,0)),f.x),mix(h(i+vec2(0,1)),h(i+vec2(1,1)),f.x),f.y);}
float fbm(vec2 p){float v=0.0,a=0.5;for(int i=0;i<4;i++){v+=a*n(p);p*=2.03;a*=0.5;}return v;}
void main(){vec2 o=vec2(uTime*0.35,uTime*0.22);
if(cameraPosition.y<vW.y){vec3 v=normalize(cameraPosition-vW);float up=clamp(-v.y,0.0,1.0);float ca=fbm(vW.xz*0.22+o*0.5);float ca2=fbm(vW.xz*0.6-o*0.8);
vec3 col=mix(vec3(0.004,0.03,0.07),vec3(0.16,0.36,0.55)*(0.6+ca*0.9)+pow(ca2,3.0)*0.5,smoothstep(0.5,0.97,up));
float dd=length(vW-cameraPosition);col=mix(col,${UW},1.0-exp(-pow(${FOGD}*dd*0.8,2.0)));gl_FragColor=vec4(tm(col),1.0);return;}
vec2 uv=vW.xz*(0.35+0.5*uGate);float e=0.12;
float c0=fbm(uv+o),cx=fbm(uv+o+vec2(e,0.0)),cz=fbm(uv+o+vec2(0.0,e));
vec3 nn=normalize(vN+vec3((c0-cx)/e,0.0,(c0-cz)/e)*(0.22+0.35*uGate));
vec3 v=normalize(cameraPosition-vW);float d=length(vW.xz-cameraPosition.xz);
nn=normalize(mix(nn,vec3(0,1,0),smoothstep(60.0,500.0,d)*0.7));
float fr=0.02+0.98*pow(1.0-max(dot(nn,v),0.0),5.0);
vec3 r=reflect(-v,nn);r.y=abs(r.y);
vec3 deep=vec3(0.002,0.016,0.04);vec3 sss=vec3(0.01,0.07,0.12)*smoothstep(-0.6,1.4,vH)*max(dot(-v,uSun)*0.5+0.5,0.0);
vec3 col=mix(deep+sss,sky(r),fr*(1.0-0.9*uGate));
float sp=pow(max(dot(r,uSun),0.0),420.0);col+=vec3(1.0,0.95,0.85)*sp*9.0*(1.0-uGate);
col+=vec3(0.9,0.95,1.0)*smoothstep(0.95,1.6,vH)*fbm(vW.xz*1.6+o*2.0)*0.12*(1.0-uGate);
float lt=clamp(dot(nn,normalize(vec3(-0.35,0.8,0.5)))*2.2-1.35+c0*0.9,0.0,1.0);col+=uGate*(vec3(0.008,0.011,0.02)+vec3(0.045,0.055,0.07)*lt);
vec3 hz=sky(normalize(vec3(-v.x,0.0005,-v.z)));hz=mix(hz,vec3(0.03,0.04,0.065),uGate);col=mix(col,hz,smoothstep(120.0,760.0,d));
gl_FragColor=vec4(tm(col),1.0);}`
const SKY_V = `varying vec3 vD;void main(){vD=normalize(position);vec4 p=projectionMatrix*modelViewMatrix*vec4(position,1.0);gl_Position=p.xyww;}`
const SKY_F = `varying vec3 vD;${SKY}
float h(vec3 p){return fract(sin(dot(p,vec3(12.9898,78.233,37.719)))*43758.5453);}
void main(){vec3 d=normalize(vD);if(cameraPosition.y<0.0){vec3 u=${UW};gl_FragColor=vec4(tm(mix(u*0.3,u*1.6,pow(d.y*0.5+0.5,2.0))),1.0);return;}vec3 c=sky(d);float st=step(0.9975,h(floor(d*420.0)))*smoothstep(0.15,0.6,d.y)*0.6;c+=vec3(st);gl_FragColor=vec4(tm(c),1.0);}`

function waveH(x: number, z: number, t: number) {
  let y = 0
  for (const w of WAVES) {
    const k = (2 * Math.PI) / w[3]
    const c = Math.sqrt(9.8 / k)
    const l = Math.hypot(w[0], w[1])
    const f = k * ((w[0] / l) * x + (w[1] / l) * z - c * t)
    y += (w[2] / k) * Math.sin(f)
  }
  return y
}
export const ss = (a: number, b: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)))
  return t * t * (3 - 2 * t)
}

/** Node shape and colour per backend node type (prototype TYPES palette). */
const TYPE_STYLE: Record<string, { color: string; shape: string }> = {
  POSITION: { color: '#D4B47A', shape: 'ring' },
  PROTOCOL: { color: '#7FA6E0', shape: 'circle' },
  MARKET: { color: '#B7A3F0', shape: 'square' },
  ASSET: { color: '#6FD3E0', shape: 'diamond' },
  SY: { color: '#D9BC82', shape: 'hexagon' },
  ORACLE: { color: '#E8A3D0', shape: 'triangle' },
  PRICE_FEED: { color: '#E8A3D0', shape: 'triangle' },
  IRM: { color: '#B8C3D4', shape: 'squareo' },
  CONTRACT: { color: '#B8C3D4', shape: 'squareo' },
  GOVERNANCE: { color: '#BFD08A', shape: 'pentagon' },
  CHAIN: { color: '#8FA3C8', shape: 'ring2' },
  BRIDGE: { color: '#8BD8F8', shape: 'drop' },
}

export interface SceneGraph {
  nodes: { id: string; type: string; unresolved: boolean }[]
  edges: { s: string; t: string; status: string }[]
  positions: string[]
}

interface Keyframe { p: number; pos: THREE_NS.Vector3; look: THREE_NS.Vector3; fov: number }

export class SeaScene {
  private T!: THREE
  private renderer!: THREE_NS.WebGLRenderer
  private scene!: THREE_NS.Scene
  private cam!: THREE_NS.PerspectiveCamera
  private fog!: THREE_NS.FogExp2
  private water!: THREE_NS.Mesh<THREE_NS.PlaneGeometry, THREE_NS.ShaderMaterial>
  private boat!: THREE_NS.Group
  private rays!: THREE_NS.Group
  private snow!: THREE_NS.Points
  private bubbles!: THREE_NS.Points
  private bub: { x: number; y: number; z: number; v: number }[] = []
  private graphGroup: THREE_NS.Group | null = null
  private graphObjs: { id: string; mesh: THREE_NS.Mesh; mat: THREE_NS.Material & { opacity: number }; glow: THREE_NS.Sprite; isPos: boolean; s: number; op: number }[] = []
  private graphEdges: { line: THREE_NS.Line; mat: THREE_NS.Material & { opacity: number }; op: number; tgt: number }[] = []
  private gc = { x: 0, y: -30, z: 0, r: 30 }
  private glowTex!: THREE_NS.Texture
  private KF: Keyframe[] = []
  private dashA = 0
  private ro: ResizeObserver | null = null
  private mount: HTMLElement
  // Adaptive quality: the water shader is costly on weak or software GPUs.
  private dpr = 1
  private frameCost = 1 / 60
  private lastAdapt = 0
  private tick = 0

  constructor(mount: HTMLElement) {
    this.mount = mount
  }

  async init(): Promise<void> {
    const T = await import('three')
    this.T = T
    const r = new T.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
    this.dpr = Math.min(window.devicePixelRatio || 1, 2)
    r.setPixelRatio(this.dpr)
    r.toneMapping = T.NoToneMapping
    r.outputColorSpace = T.SRGBColorSpace
    this.mount.appendChild(r.domElement)
    r.domElement.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block'
    this.renderer = r
    const scene = new T.Scene()
    this.scene = scene
    this.cam = new T.PerspectiveCamera(36, 1, 0.1, 3000)
    this.fog = new T.FogExp2(0x0f293d, FOGD)
    const sun = new T.Vector3(0.12, 0.075, -1).normalize()
    const water = new T.Mesh(new T.PlaneGeometry(1800, 1800, 320, 320), new T.ShaderMaterial({
      vertexShader: WATER_V, fragmentShader: WATER_F,
      uniforms: { uTime: { value: 0 }, uGate: { value: 0 }, uSun: { value: sun } }, side: T.DoubleSide,
    }))
    water.rotation.x = -Math.PI / 2
    water.frustumCulled = false
    scene.add(water)
    this.water = water
    scene.add(new T.Mesh(new T.SphereGeometry(1500, 48, 24), new T.ShaderMaterial({
      vertexShader: SKY_V, fragmentShader: SKY_F, uniforms: { uSun: { value: sun } }, side: T.BackSide, depthWrite: false,
    })))
    scene.add(new T.HemisphereLight(0x8fb0e0, 0x04101f, 1.1))
    const dl = new T.DirectionalLight(0xdfe8ff, 1.8)
    dl.position.copy(sun).multiplyScalar(100)
    scene.add(dl)
    const fill = new T.DirectionalLight(0x9fb8e0, 0.6)
    fill.position.set(-30, 20, 60)
    scene.add(fill)
    const under = new T.PointLight(0x6fa8e0, 60, 120, 1.4)
    under.position.set(0, -34, 0)
    scene.add(under)
    this.boat = this.buildBoat()
    scene.add(this.boat)

    const c = document.createElement('canvas')
    c.width = c.height = 128
    const x = c.getContext('2d')!
    const g = x.createRadialGradient(64, 64, 0, 64, 64, 64)
    g.addColorStop(0, 'rgba(255,255,255,1)')
    g.addColorStop(0.25, 'rgba(255,255,255,0.35)')
    g.addColorStop(1, 'rgba(255,255,255,0)')
    x.fillStyle = g
    x.fillRect(0, 0, 128, 128)
    this.glowTex = new T.CanvasTexture(c)

    const rayMat = () => new T.ShaderMaterial({
      uniforms: { uA: { value: 0.08 } },
      vertexShader: 'varying vec2 vU;varying vec3 vN;varying vec3 vV;void main(){vU=uv;vec4 w=modelViewMatrix*vec4(position,1.0);vN=normalize(normalMatrix*normal);vV=normalize(-w.xyz);gl_Position=projectionMatrix*w;}',
      fragmentShader: 'uniform float uA;varying vec2 vU;varying vec3 vN;varying vec3 vV;void main(){float e=pow(abs(dot(vN,vV)),2.0);float f=smoothstep(0.0,0.6,vU.y)*smoothstep(1.0,0.85,vU.y);gl_FragColor=vec4(vec3(0.5,0.72,0.92)*e*f*uA,1.0);}',
      transparent: true, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide,
    })
    const rays = new T.Group()
    for (let i = 0; i < 9; i++) {
      const m = new T.Mesh(new T.CylinderGeometry(0.6, 7 + (i % 3) * 2, 80, 32, 1, true), rayMat())
      const a = (i / 9) * Math.PI * 2
      m.position.set(Math.cos(a) * 52, -38, Math.sin(a) * 52)
      m.rotation.z = 0.16 - (i % 3) * 0.08
      m.rotation.x = 0.1
      rays.add(m)
    }
    scene.add(rays)
    this.rays = rays
    const sp: number[] = []
    for (let i = 0; i < 2600; i++) sp.push((Math.random() - 0.5) * 150, -2 - Math.random() * 80, (Math.random() - 0.5) * 150)
    const sg = new T.BufferGeometry()
    sg.setAttribute('position', new T.Float32BufferAttribute(sp, 3))
    this.snow = new T.Points(sg, new T.PointsMaterial({ color: 0xbcd6ee, size: 0.12, transparent: true, opacity: 0.45, blending: T.AdditiveBlending, depthWrite: false, fog: false }))
    scene.add(this.snow)
    const bp: number[] = []
    for (let i = 0; i < 500; i++) {
      const b = { x: -14 + Math.random() * 16, y: -16 + Math.random() * 16, z: -2 + Math.random() * 18, v: 0.04 + Math.random() * 0.09 }
      this.bub.push(b)
      bp.push(b.x, b.y, b.z)
    }
    const bg = new T.BufferGeometry()
    bg.setAttribute('position', new T.Float32BufferAttribute(bp, 3))
    this.bubbles = new T.Points(bg, new T.PointsMaterial({ color: 0xe6f2ff, size: 0.09, transparent: true, opacity: 0, blending: T.AdditiveBlending, depthWrite: false, fog: false }))
    scene.add(this.bubbles)

    const V = (a: number, b: number, d: number) => new T.Vector3(a, b, d)
    this.KF = [
      { p: 0, pos: V(-22, 20, 82), look: V(20, -0.5, 15), fov: 38 },
      { p: 0.5, pos: V(2, 11, 56), look: V(20, -1.5, 15), fov: 42 },
      { p: 0.85, pos: V(20, 0.8, 20), look: V(20, -10, 19.6), fov: 44 },
      { p: 1, pos: V(20, -6, 22), look: V(20, -28, 19), fov: 58 },
    ]
    this.ro = new ResizeObserver(() => this.resize())
    this.ro.observe(this.mount)
    this.resize()
  }

  setGraph(g: SceneGraph): void {
    const T = this.T
    if (!T) return
    if (this.graphGroup) {
      this.scene.remove(this.graphGroup)
      this.graphGroup.traverse((o) => {
        const m = o as THREE_NS.Mesh
        m.geometry?.dispose?.()
        const mat = m.material as THREE_NS.Material | THREE_NS.Material[] | undefined
        if (Array.isArray(mat)) mat.forEach((x) => x.dispose())
        else mat?.dispose?.()
      })
    }
    this.graphObjs = []
    this.graphEdges = []
    if (!g.nodes.length) return
    const ids = g.nodes.map((n) => n.id)
    const reachOf = (from: string) => {
      const m = new Map<string, number>([[from, 0]])
      let f = [from]
      let d = 0
      while (f.length) {
        d++
        const nx: string[] = []
        for (const e of g.edges) if (f.includes(e.s) && !m.has(e.t)) { m.set(e.t, d); nx.push(e.t) }
        f = nx
      }
      return m
    }
    const reaches = g.positions.map((p) => ({ p, r: reachOf(p) }))
    const depth: Record<string, number> = {}
    reaches.forEach(({ r }) => r.forEach((d, k) => { depth[k] = Math.min(depth[k] ?? 99, d) }))
    let seed = 17
    const rnd = () => { seed = (seed * 16807) % 2147483647; return seed / 2147483647 }
    const P: Record<string, { x: number; y: number; z: number; pin?: boolean }> = {}
    const R0 = 22
    g.positions.forEach((id, i) => {
      const a = (i / Math.max(1, g.positions.length)) * Math.PI * 2
      P[id] = { x: Math.cos(a) * R0, y: -22 - (i % 3) * 1.8, z: Math.sin(a) * R0, pin: true }
    })
    for (const id of ids) {
      if (P[id]) continue
      const owners = reaches.filter(({ r }) => r.has(id)).map(({ p }) => p)
      let x = 0
      let z = 0
      owners.forEach((o) => { x += P[o].x; z += P[o].z })
      const n = Math.max(1, owners.length)
      x /= n
      z /= n
      const sh = owners.length / Math.max(1, g.positions.length)
      P[id] = { x: x * (1 - sh * 0.9) * 0.8 + (rnd() - 0.5) * 6, y: -25 - (depth[id] ?? 3) * 5.2, z: z * (1 - sh * 0.9) * 0.8 + (rnd() - 0.5) * 6 }
    }
    for (let it = 0; it < 420; it++) {
      const a = 1 - it / 420
      const F: Record<string, { x: number; z: number }> = {}
      ids.forEach((i) => { F[i] = { x: 0, z: 0 } })
      for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) {
        const p = P[ids[i]], q = P[ids[j]]
        const dx = q.x - p.x, dy = q.y - p.y, dz = q.z - p.z
        const d2 = Math.max(0.5, dx * dx + dy * dy * 1.4 + dz * dz)
        if (d2 > 400) continue
        const f = 26 / d2
        const l = Math.hypot(dx, dz) || 1
        F[ids[i]].x -= (dx / l) * f; F[ids[i]].z -= (dz / l) * f
        F[ids[j]].x += (dx / l) * f; F[ids[j]].z += (dz / l) * f
      }
      for (const e of g.edges) {
        const p = P[e.s], q = P[e.t]
        if (!p || !q) continue
        const dx = q.x - p.x, dz = q.z - p.z
        const l = Math.hypot(dx, dz) || 0.01
        const f = (l - 3.5) * 0.025
        F[e.s].x += (dx / l) * f; F[e.s].z += (dz / l) * f
        F[e.t].x -= (dx / l) * f; F[e.t].z -= (dz / l) * f
      }
      ids.forEach((i) => { if (!P[i].pin) { P[i].x += F[i].x * a; P[i].z += F[i].z * a } })
    }
    const group = new T.Group()
    const geo = (s: string): THREE_NS.BufferGeometry => {
      switch (s) {
        case 'ring': return new T.TorusGeometry(1, 0.12, 16, 72)
        case 'circle': return new T.SphereGeometry(1, 32, 24)
        case 'square': return new T.BoxGeometry(1.45, 1.45, 1.45)
        case 'diamond': return new T.OctahedronGeometry(1.2)
        case 'triangle': return new T.TetrahedronGeometry(1.35)
        case 'hexagon': return new T.CylinderGeometry(1, 1, 0.8, 6)
        case 'squareo': return new T.BoxGeometry(1.4, 1.4, 1.4)
        case 'pentagon': return new T.DodecahedronGeometry(1)
        case 'ring2': return new T.TorusGeometry(1, 0.1, 12, 64)
        default: return new T.ConeGeometry(0.85, 1.6, 24)
      }
    }
    let cx = 0, cy = 0, cz = 0
    for (const n of g.nodes) {
      const st = TYPE_STYLE[n.type] ?? TYPE_STYLE.CONTRACT
      const isPos = n.type === 'POSITION'
      const s = isPos ? 1.5 * 1.3 : 0.9
      const col = new T.Color(n.unresolved ? '#6B7F99' : st.color)
      const mat = (n.unresolved || st.shape === 'squareo')
        ? new T.MeshBasicMaterial({ color: col, wireframe: true, transparent: true, opacity: 0 })
        : new T.MeshPhysicalMaterial({ color: col, emissive: col, emissiveIntensity: 0.45, roughness: 0.2, metalness: 0.2, clearcoat: 1, clearcoatRoughness: 0.15, transparent: true, opacity: 0 })
      const mesh = new T.Mesh(geo(st.shape), mat)
      mesh.scale.setScalar(s)
      mesh.position.set(P[n.id].x, P[n.id].y, P[n.id].z)
      group.add(mesh)
      const glow = new T.Sprite(new T.SpriteMaterial({ map: this.glowTex, color: col, blending: T.AdditiveBlending, depthWrite: false, transparent: true, opacity: 0, fog: false }))
      glow.scale.setScalar(s * 7)
      glow.position.copy(mesh.position)
      group.add(glow)
      this.graphObjs.push({ id: n.id, mesh, mat, glow, isPos, s, op: 0 })
      cx += P[n.id].x; cy += P[n.id].y; cz += P[n.id].z
    }
    const nN = g.nodes.length
    this.gc = { x: cx / nN, y: cy / nN, z: cz / nN, r: 0 }
    for (const n of g.nodes) this.gc.r = Math.max(this.gc.r, Math.hypot(P[n.id].x - this.gc.x, P[n.id].y - this.gc.y, P[n.id].z - this.gc.z))
    for (const e of g.edges) {
      if (!P[e.s] || !P[e.t]) continue
      const line = new T.BufferGeometry().setFromPoints([new T.Vector3(P[e.s].x, P[e.s].y, P[e.s].z), new T.Vector3(P[e.t].x, P[e.t].y, P[e.t].z)])
      const color = e.status === 'CONFIRMED' ? '#E9DFC9' : e.status === 'UNRESOLVED' ? '#6B7F99' : '#9DB4D6'
      const mat = e.status === 'CONFIRMED'
        ? new T.LineBasicMaterial({ color, transparent: true, opacity: 0 })
        : new T.LineDashedMaterial({ color, transparent: true, opacity: 0, dashSize: e.status === 'UNRESOLVED' ? 0.25 : 0.7, gapSize: e.status === 'UNRESOLVED' ? 0.6 : 0.5 })
      const l = new T.Line(line, mat)
      l.computeLineDistances()
      group.add(l)
      this.graphEdges.push({ line: l, mat, op: 0, tgt: e.status === 'CONFIRMED' ? 0.6 : e.status === 'UNRESOLVED' ? 0.32 : 0.45 })
    }
    this.scene.add(group)
    this.graphGroup = group
  }

  private buildBoat(): THREE_NS.Group {
    const T = this.T
    const g = new T.Group()
    const L = 17, W = 2.5, S = 40, M = 18
    const pos: number[] = [], col: number[] = [], idx: number[] = []
    const gold = [0.62, 0.46, 0.22], hull = [0.012, 0.02, 0.04], low = [0.05, 0.012, 0.016]
    const top = (s: number) => 1.25 + 0.75 * Math.pow(2 * s - 1, 2) + (s > 0.85 ? (0.5 * (s - 0.85)) / 0.15 : 0)
    const keel = (s: number) => -2.1 * (1 - 0.55 * Math.pow(Math.abs(2 * s - 1), 3))
    const hw = (s: number) => {
      let w = W
      if (s > 0.52) w *= Math.sqrt(Math.max(0, 1 - Math.pow((s - 0.52) / 0.48, 2)))
      if (s < 0.1) w *= 0.82 + 1.8 * s
      return w
    }
    for (let i = 0; i <= S; i++) {
      const s = i / S, x = -L / 2 + s * L, tp = top(s), kl = keel(s), w = hw(s)
      for (let j = 0; j <= M; j++) {
        const a = -Math.PI / 2 + (Math.PI * j) / M
        const z = w * Math.sin(a)
        const y = tp + (kl - tp) * Math.pow(Math.cos(a), 0.7)
        pos.push(x, y, z)
        col.push(...(y > tp - 0.14 ? gold : y < 0.05 ? low : hull))
      }
    }
    for (let i = 0; i < S; i++) for (let j = 0; j < M; j++) {
      const a = i * (M + 1) + j, b = a + M + 1
      idx.push(a, b, a + 1, b, b + 1, a + 1)
    }
    const base = pos.length / 3
    for (let i = 0; i <= S; i++) {
      const s = i / S, x = -L / 2 + s * L, tp = top(s) - 0.08, w = hw(s) * 0.97
      pos.push(x, tp, -w, x, tp, w)
      col.push(0.2, 0.12, 0.07, 0.2, 0.12, 0.07)
    }
    for (let i = 0; i < S; i++) { const a = base + i * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2) }
    const tb = pos.length / 3
    pos.push(-L / 2, top(0), 0)
    col.push(...hull)
    for (let j = 0; j < M; j++) idx.push(tb, j, j + 1)
    const hg = new T.BufferGeometry()
    hg.setAttribute('position', new T.Float32BufferAttribute(pos, 3))
    hg.setAttribute('color', new T.Float32BufferAttribute(col, 3))
    hg.setIndex(idx)
    hg.computeVertexNormals()
    g.add(new T.Mesh(hg, new T.MeshStandardMaterial({ vertexColors: true, roughness: 0.35, metalness: 0.25, side: T.DoubleSide })))
    const wood = new T.MeshStandardMaterial({ color: 0x2a1c12, roughness: 0.7 })
    const sailM = new T.MeshStandardMaterial({ color: 0xe8e1cf, roughness: 0.92, side: T.DoubleSide, emissive: 0x0c1626 })
    const cyl = (r1: number, r2: number, h: number, x: number, y: number, z: number, rx = 0, rz = 0) => {
      const m = new T.Mesh(new T.CylinderGeometry(r1, r2, h, 10), wood)
      m.position.set(x, y, z)
      m.rotation.set(rx, 0, rz)
      g.add(m)
    }
    const sail = (w: number, h: number, x: number, y: number, b: number) => {
      const pg = new T.PlaneGeometry(w, h, 12, 8)
      const p = pg.attributes.position
      for (let i = 0; i < p.count; i++) {
        const u = p.getX(i) / w + 0.5, v = p.getY(i) / h + 0.5
        p.setZ(i, b * Math.sin(Math.PI * u) * Math.sin(Math.PI * (0.15 + 0.85 * v)))
      }
      pg.computeVertexNormals()
      const m = new T.Mesh(pg, sailM)
      m.rotation.y = Math.PI / 2
      m.position.set(x, y, 0)
      g.add(m)
    }
    const masts: [number, number, number[]][] = [[-4.2, 14.5, [6.4, 5.6, 4.6, 3.6]], [0.9, 16.5, [7.2, 6.4, 5.2, 4.0]], [5.6, 15, [6.6, 5.8, 4.8, 3.8]]]
    const lines: number[][] = []
    masts.forEach(([x, h, ws], mi) => {
      const s0 = (x + L / 2) / L, d = top(s0)
      cyl(0.1, 0.17, h, x, d + h / 2, 0)
      let y = d + 2.6
      ws.forEach((w, i) => {
        const sh = [2.7, 2.4, 2.0, 1.6][i]
        cyl(0.06, 0.06, w + 0.6, x, y + sh + 0.08, 0, Math.PI / 2, 0)
        sail(w, sh, x + 0.25, y + sh / 2, 0.55)
        y += sh + 0.3
      })
      const ty = d + h
      lines.push([x, ty, 0, x - 1.2, d, hw(s0)], [x, ty, 0, x - 1.2, d, -hw(s0)], [x, ty, 0, x + 1.6, d, hw(s0) * 0.95], [x, ty, 0, x + 1.6, d, -hw(s0) * 0.95])
      if (mi < 2) {
        const nx = masts[mi + 1][0]
        lines.push([x, ty, 0, nx, top((nx + L / 2) / L) + masts[mi + 1][1] * 0.82, 0])
      }
    })
    const bx = L / 2 + 5.5, by = top(1) + 2.4
    cyl(0.06, 0.13, 6.6, L / 2 + 2.6, top(1) + 1.1, 0, 0, -Math.PI / 2 + 0.36)
    const fx = masts[2][0], fd = top((fx + L / 2) / L), fh = masts[2][1]
    const tri = (a: number[], b: number[], c: number[]) => {
      const tg = new T.BufferGeometry()
      tg.setAttribute('position', new T.Float32BufferAttribute([...a, ...b, ...c], 3))
      tg.computeVertexNormals()
      g.add(new T.Mesh(tg, sailM))
    }
    tri([bx, by, 0], [fx + 0.3, fd + fh * 0.8, 0.05], [fx + 1.2, fd + 1.8, 0.05])
    tri([bx - 1.8, by - 0.6, 0], [fx + 0.3, fd + fh * 0.6, -0.05], [fx + 2.2, fd + 1.6, -0.05])
    lines.push([bx, by, 0, fx, fd + fh, 0], [bx, by, 0, L / 2 - 0.2, top(1) - 0.2, 0])
    const mz = masts[0][0], md = top((mz + L / 2) / L)
    tri([mz - 0.15, md + 12.5, 0], [mz - 0.15, md + 2.2, 0], [-L / 2 - 1.5, md + 2.6, 0])
    lines.push([mz, md + 14.5, 0, -L / 2 + 0.3, top(0), 0])
    const lg = new T.BufferGeometry()
    lg.setAttribute('position', new T.Float32BufferAttribute(lines.flat(), 3))
    g.add(new T.LineSegments(lg, new T.LineBasicMaterial({ color: 0x0a0f18, transparent: true, opacity: 0.75 })))
    const lamp = (x: number, y: number, z: number, i: number) => {
      const m = new T.Mesh(new T.SphereGeometry(0.11, 10, 8), new T.MeshBasicMaterial({ color: 0xffd9a0 }))
      m.position.set(x, y, z)
      g.add(m)
      const pl = new T.PointLight(0xffc98a, i, 14, 2)
      pl.position.set(x, y, z)
      g.add(pl)
    }
    lamp(-L / 2 + 0.4, top(0) + 0.6, 0, 6)
    lamp(masts[1][0], top(0.55) + masts[1][1] + 0.2, 0, 3)
    lamp(L / 2 - 1, top(1) + 0.3, 0.6, 3)
    for (let i = 0; i < 7; i++) {
      const x = -5 + i * 1.7, s = (x + L / 2) / L
      const m = new T.Mesh(new T.SphereGeometry(0.07, 8, 6), new T.MeshBasicMaterial({ color: 0xffcf8a }))
      m.position.set(x, top(s) - 0.55, hw(s) + 0.01)
      g.add(m)
    }
    g.rotation.y = 0.62
    return g
  }

  private camAt(P: number) {
    const K = this.KF
    let i = 0
    while (i < K.length - 2 && P > K[i + 1].p) i++
    const a = K[i], b = K[i + 1]
    const t = Math.min(1, Math.max(0, (P - a.p) / (b.p - a.p)))
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2
    const pos = a.pos.clone().lerp(b.pos, e)
    const look = a.look.clone().lerp(b.look, e)
    return { pos, look, fov: a.fov + (b.fov - a.fov) * e }
  }

  resize(): void {
    if (!this.renderer) return
    const w = this.mount.clientWidth || 1, h = this.mount.clientHeight || 1
    this.renderer.setSize(w, h, false)
    this.cam.aspect = w / h
    this.cam.updateProjectionMatrix()
  }

  private adapt(dt: number): void {
    this.frameCost = this.frameCost * 0.9 + dt * 0.1
    const now = performance.now()
    if (this.frameCost > 1 / 28 && this.dpr > 0.5 && now - this.lastAdapt > 1500) {
      this.dpr = Math.max(0.5, this.dpr * 0.75)
      this.renderer.setPixelRatio(this.dpr)
      this.resize()
      this.lastAdapt = now
    }
  }

  /** p: 0 surface → 0.5 gate → 1 underwater dashboard. `background` lowers the cadence behind panels. */
  frame(t: number, dt: number, p: number, sway: boolean, background = false): void {
    if (!this.renderer) return
    this.adapt(dt)
    this.tick++
    if (background && this.tick % 3 !== 0) return
    const T = this.T
    this.water.material.uniforms.uTime.value = t
    this.water.material.uniforms.uGate.value = 1 - ss(0.5, 0.75, p)
    const b = this.boat
    const c = Math.cos(b.rotation.y), sn = Math.sin(b.rotation.y)
    const hC = waveH(0, 0, t), hB = waveH(7 * c, -7 * sn, t), hS = waveH(-7 * c, 7 * sn, t), hP = waveH(2 * sn, 2 * c, t), hQ = waveH(-2 * sn, -2 * c, t)
    b.position.y += (hC * 0.85 - 0.15 - b.position.y) * 0.12
    b.rotation.z += (Math.atan2(hB - hS, 14) * 0.9 - b.rotation.z) * 0.08
    b.rotation.x += (Math.atan2(hP - hQ, 4) * 0.6 - b.rotation.x) * 0.08
    const k0 = this.camAt(Math.min(1, p))
    this.dashA += dt * 0.035
    const fv = 46
    const tn = Math.tan((fv * Math.PI) / 360)
    const R = Math.max(this.gc.r, 18)
    const D = Math.max(R / (0.5 * tn * this.cam.aspect), (R * 0.75) / (0.6 * tn)) * 0.95
    const gc = new T.Vector3(this.gc.x, this.gc.y, this.gc.z)
    const dpos = gc.clone().add(new T.Vector3(Math.sin(this.dashA) * D, Math.min(D * 0.38, -6 - this.gc.y), Math.cos(this.dashA) * D))
    const w = ss(0.9, 1, p)
    const pos = k0.pos.clone().lerp(dpos, w)
    const look = k0.look.clone().lerp(gc, w)
    const fov = k0.fov + (fv - k0.fov) * w
    if (p < 0.9 && sway) {
      const sw = (1 - p) * 0.35
      pos.x += Math.sin(t * 0.21) * sw
      pos.y += Math.sin(t * 0.33) * sw * 0.6 + b.position.y * 0.3 * (1 - p)
    }
    this.cam.up.set(0, 1, 0)
    this.cam.position.copy(pos)
    this.cam.lookAt(look)
    this.cam.fov = fov
    this.cam.updateProjectionMatrix()
    const under = pos.y < waveH(pos.x, pos.z, t) - 0.1
    this.fog.density = FOGD * (1 - 0.45 * w)
    this.scene.fog = under ? this.fog : null
    this.rays.visible = under
    this.snow.visible = under
    b.visible = p < 0.95
    this.rays.children.forEach((r, i) => {
      ((r as THREE_NS.Mesh).material as THREE_NS.ShaderMaterial).uniforms.uA.value = 0.05 + 0.03 * Math.sin(t * 0.5 + i * 1.7)
    })
    this.snow.rotation.y = t * 0.006
    const bo = ss(0.76, 0.86, p) * (1 - ss(0.95, 1, p))
    ;(this.bubbles.material as THREE_NS.PointsMaterial).opacity = bo * 0.8
    if (bo > 0) {
      const a = this.bubbles.geometry.attributes.position
      this.bub.forEach((q, i) => {
        q.y += q.v
        q.x += Math.sin(t * 2 + i) * 0.01
        if (q.y > -0.3) q.y = -16
        a.setXYZ(i, q.x, q.y, q.z)
      })
      a.needsUpdate = true
    }
    // Dependency constellation: visible while diving, hidden behind the dashboard.
    const rv = ss(0.62, 0.86, p) * (1 - ss(0.94, 1, p))
    if (this.graphGroup) this.graphGroup.visible = rv > 0.005
    for (const o of this.graphObjs) {
      const tgt = rv * (o.isPos ? 0.95 : 0.7)
      o.op += (tgt - o.op) * 0.08
      o.mat.opacity = o.op
      ;(o.glow.material as THREE_NS.SpriteMaterial).opacity = Math.min(1, o.op) * (o.isPos ? 0.7 : 0.4)
      o.mesh.rotation.y += o.isPos ? 0.005 : 0.003
      if (o.isPos) o.mesh.rotation.x = Math.PI / 2 + Math.sin(t * 0.6 + o.s) * 0.25
    }
    for (const e of this.graphEdges) {
      e.op += (rv * e.tgt - e.op) * 0.08
      e.mat.opacity = e.op
      e.line.visible = e.op > 0.005
    }
    this.renderer.render(this.scene, this.cam)
  }

  dispose(): void {
    this.ro?.disconnect()
    this.renderer?.dispose()
    this.renderer?.domElement.remove()
  }
}
