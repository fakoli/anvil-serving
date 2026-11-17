---
title: Anvil Serving
description: Operate, qualify, and expose local AI capabilities through explicit, reviewable contracts.
hide:
  - navigation
  - toc
---

<div class="av-home" markdown="1">

<section class="av-hero">
<div>
<p class="av-kicker">Local AI, operated on purpose</p>
<h1>Operate, qualify, and expose <em>local AI</em>.</h1>
<p class="av-hero__lead">One CLI to serve models on your own GPUs, give every client one authenticated endpoint, and prove what works with retained evidence. Nothing swaps your model behind your back.</p>
<div class="av-actions">
<a href="GETTING-STARTED/" class="md-button md-button--primary">Get started</a>
<a href="recipes/" class="md-button">Browse recipes</a>
<a href="benchmarks/results/" class="md-button">Benchmark results</a>
</div>
</div>
<div class="av-terminal" aria-label="Quickstart commands">
<div class="av-terminal__bar"><i></i><i></i><i></i><span>gateway in front of a server you already run</span></div>
<pre><code><span class="p">$ </span>pip install anvil-serving
<span class="p">$ </span>anvil-serving init --gateway-only --upstream http://127.0.0.1:11434/v1 --model llama3.2
<span class="p">$ </span>anvil-serving router run
<span class="c"># one authenticated endpoint: Anthropic Messages, OpenAI Chat, Responses</span></code></pre>
</div>
</section>

<section class="av-section">
<h2>One contract, four facts</h2>
<p>Configuration, runtime state, qualification, and exposure stay separate. Every transition is an explicit command you can preview.</p>
<div class="av-flow">
<div><strong>Declare</strong><span>Models, aliases, and ownership live in configuration you can review.</span></div>
<div><strong>Operate</strong><span>Start, stop, and watch serves inside those boundaries.</span></div>
<div><strong>Qualify</strong><span>Preflight and benchmark. Evidence never promotes a model by itself.</span></div>
<div><strong>Expose</strong><span>Route exact aliases through one authenticated gateway. No fallback.</span></div>
</div>
</section>

<section class="av-section">
<h2>Seven journeys</h2>
<p>Pick the outcome you need. Each journey is an ordered list of supported commands with a stated boundary.</p>
<!-- product-journey:cards -->
</section>

<section class="av-section">
<h2>Latest benchmark results</h2>
<p>Every finding publishes in the same record format: what was measured, on which hardware, and what was decided. Results are dated measurements, not live deployment status.</p>
<!-- benchmark-records:latest:6 -->
<div class="av-actions">
<a href="benchmarks/results/" class="md-button">Explore all results</a>
<a href="benchmarks/" class="md-button">How to read them</a>
</div>
</section>

<section class="av-section">
<h2>Take it with you</h2>
<p>Everything here is meant to leave the site and run on your hardware.</p>
<div class="av-grid">
<a class="av-card" href="recipes/"><span class="av-card__kicker">Download</span><span class="av-card__title">Serve recipes</span><span class="av-card__body">Every published recipe as a file you can load, or all of them as one zip.</span><span class="av-card__go">Browse recipes →</span></a>
<a class="av-card" href="cheat-sheet/"><span class="av-card__kicker">One page</span><span class="av-card__title">Operator cheat sheet</span><span class="av-card__body">The commands for all seven journeys, in order, ready to print.</span><span class="av-card__go">Open the cheat sheet →</span></a>
<a class="av-card" href="benchmarks/results/"><span class="av-card__kicker">Data</span><span class="av-card__title">Benchmark index</span><span class="av-card__body">Every record, capacity point, and quality result as one JSON file.</span><span class="av-card__go">Open results →</span></a>
<a class="av-card" href="CLI/"><span class="av-card__kicker">Reference</span><span class="av-card__title">Command index</span><span class="av-card__body">Every command family, option, and execution policy.</span><span class="av-card__go">Open the CLI reference →</span></a>
</div>
</section>

</div>
