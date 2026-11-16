---
title: Anvil Serving
description: Operate, qualify, and expose local AI capabilities through explicit, reviewable contracts.
hide:
  - navigation
  - toc
---

<div class="anvil-home">

<section class="anvil-hero" aria-labelledby="anvil-hero-title">
  <div class="anvil-hero__copy">
    <h1 id="anvil-hero-title">Anvil Serving</h1>
    <p class="anvil-hero__tagline">Operate, qualify, and expose local AI capabilities through explicit, reviewable contracts.</p>
    <p class="anvil-hero__summary">One package, one CLI, and one release line for seven journeys: Model Serving, the Capability Gateway, Evaluation &amp; Evidence, Anvil Voice, Anvil Media, Anvil Connect, and Control Plane &amp; Fleet. Every journey has a stated boundary and a sequence of supported commands, and nothing swaps your model behind your back.</p>
    <div class="anvil-actions">
      <a href="GETTING-STARTED/" class="md-button md-button--primary">Get started</a>
      <a href="QUICKSTART-GATEWAY/" class="md-button">Gateway quickstart</a>
      <a href="benchmarks/results/" class="md-button md-button--evidence">Benchmark results</a>
    </div>
  </div>
  <div class="anvil-hero__visual">
    <picture>
      <source srcset="assets/hero-anvil.webp" type="image/webp">
      <img src="assets/hero-anvil.png" alt="" width="768" height="664" loading="lazy" decoding="async" fetchpriority="low" aria-hidden="true">
    </picture>
  </div>
</section>

<section class="anvil-section" aria-labelledby="product-families">
  <div class="anvil-section__heading">
    <h2 id="product-families">Seven journeys, one product</h2>
    <p>Start with the outcome you need. Each journey page lists its steps as supported commands, states what it will and will not do, and links to the detailed guides.</p>
  </div>
  <div class="anvil-family-list">
    <a href="journeys/model-serving/">
      <strong>Model Serving</strong>
      <span>Run a pinned recipe on your GPUs: preview it, start it, watch it.</span>
      <em>Serve a model</em>
    </a>
    <a href="journeys/capability-gateway/">
      <strong>Capability Gateway</strong>
      <span>Give every client one authenticated endpoint with exact capability aliases.</span>
      <em>Expose capabilities</em>
    </a>
    <a href="journeys/evaluation-evidence/">
      <strong>Evaluation &amp; Evidence</strong>
      <span>Prove an endpoint works, measure it, and keep the evidence.</span>
      <em>Qualify a model</em>
    </a>
    <a href="journeys/anvil-voice/">
      <strong>Anvil Voice</strong>
      <span>Operate speech-to-text, text-to-speech, and realtime voice.</span>
      <em>Run voice</em>
    </a>
    <a href="journeys/anvil-media/">
      <strong>Anvil Media</strong>
      <span>Generate images and video through named, durable workflows.</span>
      <em>Run media workflows</em>
    </a>
    <a href="journeys/anvil-connect/">
      <strong>Anvil Connect</strong>
      <span>Reach local web apps through one self-hosted, signed-in edge.</span>
      <em>Publish local apps</em>
    </a>
    <a href="journeys/control-plane-fleet/">
      <strong>Control Plane &amp; Fleet</strong>
      <span>Declare host ownership, dispatch typed operations, see the whole fleet.</span>
      <em>Operate the fleet</em>
    </a>
  </div>
</section>

<section class="anvil-section" aria-labelledby="private-network">
  <div class="anvil-section__heading">
    <h2 id="private-network">Local services, one private network</h2>
    <p>Keep model, voice, media, router, and controller processes on each owning device's loopback interface. Tailscale supplies private device reachability and identity policy; Tailscale Serve publishes only the reviewed front doors.</p>
  </div>
  <div class="anvil-resource-list">
    <a href="HOST-SERVICES/"><strong>Manage host services</strong><span>Discover, adopt, inspect, start, and stop services through Anvil. Use Docker on Windows and Linux, or native MLX and Docker on macOS.</span></a>
    <a href="TAILSCALE-NETWORKING/"><strong>Set up private networking</strong><span>Understand device roles, identity, grants, MagicDNS, mobile access, and the Serve-to-loopback pattern.</span></a>
    <a href="DEVICE-TOPOLOGIES/"><strong>Place roles across devices</strong><span>Separate primary inference, harness, voice, media, and operator ownership without exposing machine identity.</span></a>
  </div>
</section>

<section class="anvil-section anvil-section--evidence" aria-labelledby="current-evidence">
  <div class="anvil-section__heading">
    <h2 id="current-evidence">Benchmarks and recipes</h2>
    <p>Every result is a dated finding with its raw artifacts. Results describe what was measured on named hardware; they are not a claim about what any deployment runs today.</p>
  </div>
  <div class="anvil-resource-list">
    <a class="anvil-path--evidence" href="benchmarks/results/"><strong>Benchmark results</strong><span>Every finding as one record — model, engine, quantization, hardware, context, concurrency, decision — with throughput and latency charts.</span></a>
    <a href="recipes/"><strong>Serve recipes</strong><span>Every published recipe with a page, a download, and the findings that measured it.</span></a>
    <a href="benchmarks/comparison/"><strong>Compare models</strong><span>Measured configurations side by side.</span></a>
    <a href="benchmarks/voice-models/"><strong>Voice model results</strong><span>Speech-to-text, text-to-speech, and answer-model comparisons.</span></a>
    <a href="benchmarks/models/"><strong>Model dossiers</strong><span>Status, recipe, and decision boundary by model.</span></a>
    <a href="findings/"><strong>Dated findings</strong><span>Sanitized evidence snapshots, retained as published.</span></a>
  </div>
</section>

<section class="anvil-section anvil-section--contract" aria-labelledby="operating-contract">
  <div class="anvil-section__heading">
    <h2 id="operating-contract">The operating contract</h2>
    <p>Configuration, runtime state, qualification, and exposure are separate facts. Anvil Serving keeps those transitions explicit.</p>
  </div>
  <ol class="anvil-contract">
    <li>
      <strong>Declare</strong>
      <span>Define models, aliases, policies, and authority boundaries as configuration.</span>
    </li>
    <li>
      <strong>Operate</strong>
      <span>Run serving, gateway, voice, media, and fleet workflows inside those boundaries.</span>
    </li>
    <li>
      <strong>Qualify</strong>
      <span>Measure with preflight and benchmarks. Evidence never auto-promotes.</span>
    </li>
    <li>
      <strong>Expose</strong>
      <span>Publish only approved capabilities through explicit, authenticated contracts.</span>
    </li>
  </ol>
</section>

<section class="anvil-section" aria-labelledby="reference">
  <div class="anvil-section__heading">
    <h2 id="reference">Reference</h2>
    <p>Use these pages when you need exact contracts, commands, or failure guidance.</p>
  </div>
  <div class="anvil-resource-list anvil-resource-list--reference">
    <a href="ARCHITECTURE/"><strong>Architecture</strong><span>Components, authority planes, and deployment shapes.</span></a>
    <a href="PRODUCT-FAMILIES/"><strong>Product map</strong><span>Family boundaries and ordered user journeys.</span></a>
    <a href="CONFIGURATION/"><strong>Configuration</strong><span>Router, serve, voice, media, and fleet contracts.</span></a>
    <a href="CLI/"><strong>Command index</strong><span>Every command family, policy, and option.</span></a>
    <a href="TROUBLESHOOTING/"><strong>Troubleshooting</strong><span>Diagnose aliases, serves, authentication, and preflight.</span></a>
    <a href="TERMINOLOGY/"><strong>Terminology</strong><span>Shared vocabulary for routes, serves, topology, and evidence.</span></a>
  </div>
</section>

</div>
