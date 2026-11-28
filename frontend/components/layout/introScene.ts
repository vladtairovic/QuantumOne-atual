import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

const URL_MODEL = "/intro/quantumone-q1.glb";
const ARRIVAL = 4.05;
const END = 5;
const TAU = Math.PI * 2;

function progress(time: number, from: number, to: number) {
  const t = THREE.MathUtils.clamp((time - from) / (to - from), 0, 1);
  return t * t * (3 - 2 * t);
}

function noise(seed: number) {
  const v = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return v - Math.floor(v);
}

// An HDR studio panorama. Long tilted softboxes produce streaks that travel
// across the original beveled faces as the object and its lighting rig turn.
function studioLighting() {
  const width = 256, height = 128;
  const pixels = new Float32Array(width * height * 4);
  const strip = (value: number, center: number, breadth: number) =>
    Math.exp(-Math.pow((value - center) / breadth, 4));
  for (let y = 0; y < height; y++) {
    const latitude = ((y + 0.5) / height - 0.5) * Math.PI;
    for (let x = 0; x < width; x++) {
      const angle = ((x + 0.5) / width - 0.5) * TAU;
      const box = strip(angle + latitude * 0.55, 1.48, 0.29) * strip(latitude, 0.18, 0.86);
      const silk = strip(angle - latitude * 0.3, 2.22, 0.55) * strip(latitude, 0.18, 1.02);
      const edge = strip(Math.sin(angle - latitude * 0.32 + 0.7), 0, 0.10);
      const ceiling = strip(latitude, 0.99, 0.25);
      const i = (y * width + x) * 4;
      pixels[i] = 0.22 + box * 9.5 + silk * 1.45 + edge * 3 + ceiling * 1.4;
      pixels[i + 1] = 0.17 + box * 8.7 + silk * 0.8 + edge * 1.8 + ceiling * 1.15;
      pixels[i + 2] = 0.38 + box * 10 + silk * 3.4 + edge * 7 + ceiling * 2.5;
      pixels[i + 3] = 1;
    }
  }
  const texture = new THREE.DataTexture(pixels, width, height, THREE.RGBAFormat, THREE.FloatType);
  texture.mapping = THREE.EquirectangularReflectionMapping;
  texture.needsUpdate = true;
  return texture;
}

// A field at optical infinity. Only the wave crests catch light.
function quantumField() {
  const time = { value: 0 }, aspect = { value: 1 };
  const field = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), new THREE.ShaderMaterial({
    uniforms: { time, aspect }, depthWrite: false, depthTest: false,
    vertexShader: `varying vec2 screen;
      void main() { screen = position.xy; gl_Position = vec4(position.xy, 0.999, 1.0); }`,
    fragmentShader: `uniform float time, aspect;
      varying vec2 screen;
      void main() {
        vec2 p = vec2(screen.x * aspect, screen.y);
        vec3 light = vec3(0.003, 0.004, 0.013);
        light += vec3(0.019, 0.011, 0.045) * exp(-0.85 * dot(p,p));
        for (int i = 0; i < 5; ++i) {
          float layer = float(i);
          float wave = -0.48 + 0.13 * sin(p.x * 1.8 + time * 0.17 + layer * 0.24)
              + layer * 0.029 + 0.04 * cos(p.x * 3.0 - time * 0.13);
          float d = abs(p.y - wave);
          float width = max(fwidth(p.y), 0.001);
          light += vec3(0.024, 0.027, 0.068) * exp(-d / width) + vec3(0.001, 0.002, 0.004) * exp(-d * 29.0);
        }
        gl_FragColor = vec4(light, 1.);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }`,
  }));
  field.frustumCulled = false;
  field.renderOrder = -100;
  return { field, time, aspect };
}

function assemblyGeometry(original: THREE.BufferGeometry) {
  const geometry = original.index ? original.toNonIndexed() : original.clone();
  const vertices = geometry.getAttribute("position");
  const shards = new Float32Array(vertices.count * 4);
  // Give every triangle in a small spatial cell the same flight path. The
  // three vertices of each triangle stay together throughout the assembly.
  for (let i = 0; i < vertices.count; i += 3) {
    const x = (vertices.getX(i) + vertices.getX(i + 1) + vertices.getX(i + 2)) / 3;
    const y = (vertices.getY(i) + vertices.getY(i + 1) + vertices.getY(i + 2)) / 3;
    const seed = Math.floor((x + 0.5) * 14) + 23 * Math.floor((y + 0.5) * 16);
    const dx = (noise(seed) - 0.58) * 1.0;
    const dy = (noise(seed + 1.2) - 0.28) * 0.85;
    const dz = (noise(seed + 5.1) - 0.42) * 0.96;
    const delay = (x + 0.5) * 0.28 + noise(seed + 1.7) * 0.27;
    for (let j = i; j < i + 3; j++) shards.set([dx, dy, dz, delay], j * 4);
  }
  geometry.setAttribute("assemblyShard", new THREE.BufferAttribute(shards, 4));
  return geometry;
}

function makeSymbol(original: THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial>) {
  const geometry = assemblyGeometry(original.geometry);
  const material = original.material.clone();
  material.fog = false;
  material.color.lerp(new THREE.Color(0xa268f4), 0.30);
  material.roughness = 0.19;
  material.envMapIntensity = 1.16;
  const assemble = { value: 0 }, sweep = { value: -1.15 };
  material.onBeforeCompile = shader => {
    shader.uniforms.uAssembly = assemble;
    shader.uniforms.uSweep = sweep;
    shader.vertexShader = shader.vertexShader.replace("#include <common>", `
      #include <common>
      attribute vec4 assemblyShard;
      uniform float uAssembly;
      varying float settled;
      varying vec3 surfacePoint;
    `).replace("#include <begin_vertex>", `
      #include <begin_vertex>
      settled = smoothstep(assemblyShard.w, assemblyShard.w + 0.44, uAssembly);
      float remaining = 1.0 - settled;
      float orbit = remaining * (1.4 + assemblyShard.y);
      vec2 drift = mat2(cos(orbit), -sin(orbit), sin(orbit), cos(orbit)) * assemblyShard.xy;
      transformed += remaining * vec3(drift, assemblyShard.z);
      transformed.y += sin(settled * 3.14159) * assemblyShard.x * 0.16;
      surfacePoint = position;
    `);
    shader.fragmentShader = shader.fragmentShader.replace("#include <common>", `
      #include <common>
      varying float settled;
      varying vec3 surfacePoint;
      uniform float uSweep;
    `).replace("#include <roughnessmap_fragment>", `
      #include <roughnessmap_fragment>
      float grain = dot(surfacePoint.xy, vec2(92.0, 780.0));
      roughnessFactor += 0.037 * sin(grain) / (1.0 + fwidth(grain));
    `).replace("#include <emissivemap_fragment>", `
      #include <emissivemap_fragment>
      float cell = 0.5 + 0.5 * sin(surfacePoint.x * 23.0 + 2.0 * sin(surfacePoint.y * 17.0));
      float deposition = settled * 1.36 - cell * 0.32;
      if (deposition < 0.065) discard;
      float front = 1.0 - smoothstep(0.065, 0.31, deposition);
      totalEmissiveRadiance += front * vec3(1.6, 2.1, 3.8)
          + vec3(0.34, 0.085, 0.85) * sin(settled * 3.14159);
    `).replace("#include <opaque_fragment>", `
      // Local reflection of the moving foreground strip box. Parallax across
      // a flat face is stronger here than in the infinitely distant HDR rig.
      vec3 reflected = reflect(-normalize(vViewPosition), normal);
      vec2 card = surfacePoint.xy + reflected.xy * (0.78 / max(0.12, reflected.z));
      float across = dot(card, vec2(0.93, -0.37)) - uSweep;
      float strip = exp(-across * across * 70.0);
      float edgeOffset = (across + 0.12) * 76.0;
      float seam = exp(-edgeOffset * edgeOffset);
      outgoingLight = outgoingLight * 0.71 + vec3(1.05, 0.75, 1.28)
        * (strip * 0.86 + seam * 0.42) * smoothstep(0.45, 0.94, reflected.z);
      #include <opaque_fragment>
    `);
  };
  return { mesh: new THREE.Mesh(geometry, material), assemble, sweep };
}

// Each character travels through 3D space to a vertex sampled from the GLB.
function makeCode(geometry: THREE.BufferGeometry) {
  const atlas = document.createElement("canvas");
  atlas.width = 512; atlas.height = 64;
  const brush = atlas.getContext("2d");
  if (!brush) throw new Error("Não foi possível preparar os glifos 3D.");
  brush.fillStyle = "white"; brush.font = "bold 50px monospace";
  brush.textAlign = "center"; brush.textBaseline = "middle";
  Array.from("01{}<>;/").forEach((glyph, i) => brush.fillText(glyph, i * 64 + 32, 34));
  const texture = new THREE.CanvasTexture(atlas);
  const vertices = geometry.getAttribute("position");
  const count = 176;
  const plane = new THREE.PlaneGeometry(1, 1);
  const instances = new THREE.InstancedBufferGeometry();
  instances.index = plane.index;
  instances.setAttribute("position", plane.getAttribute("position"));
  instances.setAttribute("uv", plane.getAttribute("uv"));
  const targets = [], flights = [];
  for (let i = 0; i < count; i++) {
    const v = Math.floor(noise(i + 8.7) * vertices.count);
    targets.push(vertices.getX(v), vertices.getY(v), vertices.getZ(v));
    flights.push(noise(i + 0.1), noise(i + 0.36), noise(i + 4.1), noise(i + 6.1));
  }
  instances.instanceCount = count;
  instances.setAttribute("targetPoint", new THREE.InstancedBufferAttribute(new Float32Array(targets), 3));
  instances.setAttribute("flight", new THREE.InstancedBufferAttribute(new Float32Array(flights), 4));
  const phase = { value: 0 };
  const material = new THREE.ShaderMaterial({
    uniforms: { atlas: { value: texture }, phase },
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: `
      attribute vec3 targetPoint;
      attribute vec4 flight;
      varying vec2 glyphUv;
      varying vec3 tint;
      uniform float phase;
      void main() {
        float arrival = smoothstep(flight.x * 0.45, 0.49 + flight.x * 0.51, phase);
        float left = 1.0 - arrival;
        float a = 6.28318 * flight.y + arrival * 2.65;
        vec3 center = targetPoint + vec3(cos(a) * (0.28 + flight.z), sin(a) * (0.25 + flight.z * 0.7),
           (flight.z - 0.40) * 1.05) * left;
        vec4 mv = modelViewMatrix * vec4(center, 1.0);
        mv.xy += position.xy * (0.035 + flight.w * 0.052) * smoothstep(0.0, 0.19, left);
        gl_Position = projectionMatrix * mv;
        glyphUv = vec2((floor(flight.w * 8.0) + uv.x) / 8.0, uv.y);
        tint = mix(vec3(0.57, 0.42, 1.0), vec3(0.75, 0.93, 1.0), flight.z);
      }
    `,
    fragmentShader: `
      uniform sampler2D atlas;
      varying vec2 glyphUv;
      varying vec3 tint;
      void main() {
        float ink = texture2D(atlas, glyphUv).a;
        if (ink < 0.07) discard;
        gl_FragColor = vec4(tint, ink * 0.85);
      }
    `,
  });
  const mesh = new THREE.Mesh(instances, material);
  mesh.frustumCulled = false;
  return { mesh, texture, phase };
}

export async function createIntroScene(canvas: HTMLCanvasElement, signal: AbortSignal) {
  const response = await fetch(URL_MODEL, { signal });
  if (!response.ok) throw new Error("Falha ao carregar a logo 3D.");
  const gltf = await new GLTFLoader().parseAsync(await response.arrayBuffer(), "/intro/");
  const original = gltf.scene.getObjectByName("QuantumOne_Q1") as THREE.Mesh<THREE.BufferGeometry, THREE.MeshStandardMaterial> | undefined;
  if (!original?.isMesh || !original.material.isMeshStandardMaterial) throw new Error("Modelo QuantumOne inválido.");
  if (signal.aborted) {
    original.geometry.dispose(); original.material.dispose(); signal.throwIfAborted();
  }

  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "high-performance" });
  const gl = renderer.getContext();
  const debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
  const softwareGpu = debugInfo && /swiftshader|llvmpipe|software/i.test(String(gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)));
  if (softwareGpu) {
    original.geometry.dispose(); original.material.dispose();
    renderer.dispose(); renderer.forceContextLoss();
    throw new Error("Usar projeção vetorial em renderização por software.");
  }
  renderer.autoClear = false;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.08;
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x0b0a15, 0.092);
  const camera = new THREE.PerspectiveCamera(29, 1, 0.07, 38);
  const environment = studioLighting();
  scene.environment = environment;
  const field = quantumField();
  scene.add(field.field);
  const rim = new THREE.DirectionalLight(0xc2a2ff, 3.1);
  rim.position.set(-2, 2.2, -1);
  const key = new THREE.DirectionalLight(0xfff4ef, 2.35);
  const bounce = new THREE.PointLight(0x9863ff, 1.1, 7, 2);
  bounce.position.set(0, -0.6, 0.65);
  scene.add(rim, key, bounce);

  const hero = new THREE.Group();
  const symbol = makeSymbol(original);
  const pieces = symbol.mesh.geometry;
  const solid = original.geometry;
  solid.setAttribute("assemblyShard", new THREE.BufferAttribute(new Float32Array(solid.getAttribute("position").count * 4), 4));
  const code = makeCode(original.geometry);
  hero.add(symbol.mesh, code.mesh); scene.add(hero);
  const cutScene = new THREE.Scene();
  const cut = new THREE.Mesh(original.geometry, new THREE.ShaderMaterial({
    depthTest: false, depthWrite: false, side: THREE.DoubleSide, blending: THREE.NoBlending,
    vertexShader: "void main(){ gl_Position = projectionMatrix * modelViewMatrix * vec4(position.xy, 0., 1.); }",
    fragmentShader: "void main(){ gl_FragColor = vec4(0.); }",
  }));
  cut.frustumCulled = false;
  cutScene.add(cut);
  // Interior of the solid right stroke, measured from the supplied mesh.
  // Scaling about the empty hole of the Q would leave an island at its center.
  const cutAnchor = new THREE.Vector2(0.336, 0.133);
  const target = new THREE.WebGLRenderTarget(1, 1, {
    type: THREE.HalfFloatType, samples: Math.min(2, renderer.capabilities.maxSamples),
  });
  const composer = new EffectComposer(renderer, target);
  const base = new RenderPass(scene, camera);
  const bloom = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.40, 0.36, 0.95);
  const output = new OutputPass();
  composer.addPass(base); composer.addPass(bloom); composer.addPass(output);
  function resize(w: number, h: number, ratio: number) {
    renderer.setDrawingBufferSize(w, h, ratio);
    composer.setSize(Math.round(w * ratio), Math.round(h * ratio));
    const glowRatio = Math.min(ratio, Math.sqrt(480000 / (w * h)));
    bloom.setSize(Math.round(w * glowRatio), Math.round(h * glowRatio));
  }
  let width = 0, height = 0;
  let lastTime = 0;
  let useBloom = true;
  let disposed = false;

  function render(seconds: number) {
    if (disposed) return;
    const w = Math.max(canvas.clientWidth, 1), h = Math.max(canvas.clientHeight, 1);
    const frameInterval = seconds - lastTime;
    if (frameInterval > 0.15 && lastTime > 0) useBloom = false;
    lastTime = seconds;
    if (w !== width || h !== height) {
      width = w; height = h;
      const pixelRatio = Math.max(1, Math.min(window.devicePixelRatio || 1, 2, Math.sqrt(8300000 / (w * h))));
      resize(w, h, pixelRatio);
      camera.aspect = w / h; camera.updateProjectionMatrix();
    }
    if (seconds >= END) {
      renderer.setClearColor(0, 0); renderer.clear(); return;
    }
    const t = Math.max(0, seconds);
    const travel = progress(t, 0, ARRIVAL);
    const build = progress(t, -0.10, 1.64);
    const spin = progress(t, 1.18, 3.91);
    const widthFactor = Math.min(1, camera.aspect * 1.15);
    const approach = 1 - travel;
    camera.position.set(-0.21 * approach, 0.16 * approach, 3.85 / widthFactor + approach * 0.23);
    camera.lookAt(0, 0, 0);
    // A cubic Bézier flight with a shallow overshoot; easing occurs along the
    // entire path, so velocity stays continuous through both changes of course.
    const a = approach * approach * approach, b = 3 * approach * approach * travel, c = 3 * approach * travel * travel;
    hero.position.set((-0.84 * a - 0.56 * b + 0.54 * c) * widthFactor,
      0.29 * a - 0.66 * b + 0.10 * c, -1.32 * a + 0.4 * b + 0.24 * c);
    hero.scale.setScalar(1.08 - 0.16 * approach);
    hero.rotation.set(-0.29 * approach + 0.09 * Math.sin(spin * TAU),
      -0.66 * approach + TAU * spin, 0.21 * approach - 0.13 * Math.sin(travel * Math.PI));
    symbol.assemble.value = build;
    // After assembly the indexed GLB shares vertices across adjacent triangles.
    symbol.mesh.geometry = build < 1 ? pieces : solid;
    symbol.sweep.value = -1.1 + 1.32 * progress(t, 3.02, ARRIVAL);
    code.phase.value = build; code.mesh.visible = build < 1;
    scene.environmentRotation.y = -0.91 + 0.88 * progress(t, 0.4, 2) + 0.77 * progress(t, 2.80, ARRIVAL);
    scene.environmentRotation.z = -0.18;
    key.position.set(-2.0 + 4.2 * progress(t, 2.50, ARRIVAL), 2.1, 3.5);
    field.time.value = t; field.aspect.value = camera.aspect;
    renderer.setClearColor(0x0b0a15, 1);
    renderer.clear();
    if (useBloom) composer.render();
    else renderer.render(scene, camera);
    if (t > ARRIVAL) {
      const opening = (t - ARRIVAL) / (END - ARRIVAL);
      const viewHeight = 2 * camera.position.z * Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
      const cover = 6 * viewHeight * Math.hypot(1, camera.aspect) + 12;
      const scale = 1.08 * Math.pow(cover / 1.08, opening * opening);
      cut.scale.set(scale, scale, 1);
      cut.position.set((1.08 - scale) * cutAnchor.x, (1.08 - scale) * cutAnchor.y, 0);
      renderer.render(cutScene, camera);
    }
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
    for (const group of [scene, cutScene]) group.traverse(object => {
      const drawable = object as THREE.Mesh;
      if (drawable instanceof THREE.InstancedMesh) drawable.dispose();
      if (drawable.geometry) geometries.add(drawable.geometry);
      if (drawable.material) {
        for (const material of Array.isArray(drawable.material) ? drawable.material : [drawable.material]) materials.add(material);
      }
    });
    geometries.add(pieces);
    geometries.forEach(geometry => geometry.dispose());
    materials.forEach(material => material.dispose());
    original!.material.dispose(); environment.dispose(); code.texture.dispose();
    bloom.dispose(); output.dispose(); composer.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  }
  try {
    render(0);
    await renderer.compileAsync(scene, camera);
    signal.throwIfAborted();
    return { render, dispose };
  } catch (error) {
    dispose();
    throw error;
  }
}
