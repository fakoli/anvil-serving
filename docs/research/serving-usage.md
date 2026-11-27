# How the reference deployment is actually used

**Measured:** 2026-11-26. This is a sanitized usage snapshot of one operator's
deployment, written to ground the serving-research scan in
[serving-scan-hindsight.md](serving-scan-hindsight.md). It is not a benchmark,
a qualification, or a design decision.

## Method and limits

- **Sources.** The gateway's protected usage accounting (`anvil-serving router
  usage`), the capability reachability report (`router fleet-status`), non-secret
  key labels and grants (`router keys list`), and two weeks of host and engine
  metrics from the operator's Prometheus. All reads were through supported,
  read-only surfaces.
- **No content.** Usage accounting holds counts, timings, and token totals only.
  No prompt or response text was read, and no raw log file was opened.
- **Windows.** Request detail covers 30.9 days (2026-10-27 to 2026-11-26 UTC,
  58,667 requests). Daily totals cover 2026-10-09 to 2026-11-26 (103,915
  requests). Host and engine metrics cover the last 14 days at 5-minute
  resolution, which is that store's retention.
- **Coverage is incomplete.** The accounting store reports open coverage gaps
  over its whole history, so every total here is a lower bound.
- **Sanitized.** Hosts appear by role, callers by journey, and model identities
  are withheld; see [OPERATOR-PRIVACY.md](../OPERATOR-PRIVACY.md). Local time is
  the operator's.
- **Three things the gateway does not record today**, which limit this report:
  admission wait and time to first content are empty on all 58,667 records; the
  error-code field is empty on every failed request; and usage accounting holds
  no session identifier by design.

## Headlines

1. **Four in five gateway requests are memory-plane traffic, not inference.**
   Chat is 19% of requests.
2. **One alias on one host carries the inference load.** `llm.primary` is 98.8%
   of chat requests, and 99.1% of dispatched chat ran on the primary node.
3. **The primary engine is idle about 97% of the time.** A second request is in
   flight for 0.26% of wall time.
4. **Prompts are long and repeat heavily.** The median `llm.primary` prompt is
   28,907 tokens, and 90.5% of input tokens are served from the prefix cache.
5. **No host is short of compute.** The scarce things are the engine's
   conversation-cache budget, which is full all the time, and the secondary
   tier, which failed 91% of the requests sent to it.

## Who calls it

Chat requests over 30 days, grouped by caller journey. Shares are of the 11,173
chat requests.

| Journey | Requests | Share | Input tokens | Cached | Output tokens | Shape |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Long-context agent harness | 4,649 | 41.6% | 402.8M | 96% | 4.20M | Very large prompts, modest replies |
| Memory fact-extraction service | 2,616 | 23.4% | 18.4M | 33% | 9.62M | Short prompts, long reasoning-heavy output |
| Coding agent | 1,624 | 14.5% | 72.4M | 61% | 0.85M | Large prompts, tool-call replies |
| Heartbeat-style agent | 1,025 | 9.2% | 123.7M | 92% | 0.07M | About 120k tokens in, about 70 out |
| Shared master credential | 851 | 7.6% | 2.7M | 70% | 0.37M | Small prompts |
| Chat UI | 274 | 2.5% | 6.4M | 90% | 0.09M | Interactive chat |
| Glasses (dictation and agent) | 97 | 0.9% | under 0.1M | 87% | under 0.1M | First day of use in the window |
| Scripts | 31 | 0.3% | 0.3M | 0% | under 0.1M | Batch generation |

Two things stand out. The fact-extraction service is a background caller, yet
it produces 63% of all output tokens. And the heartbeat-style agent sends very
large prompts for almost no output, so nearly all of its cost is prefill that
the prefix cache absorbs.

Beyond chat, the same 30 days held 47,397 memory requests (median 1,496 per
day), 74 speech-to-text requests, and 23 text-to-speech requests. Ninety percent
of memory requests were answered by the gateway without an upstream dispatch.
The dispatched ones had a median latency of 36 ms and a 99th percentile of 35 s.

## By alias and backing serve

| Alias | Chat requests | Success | Backing tier | Notes |
| --- | ---: | ---: | --- | --- |
| `llm.primary` | 11,035 | 94.4% | Primary node | Four different serves backed it during the window, one at a time |
| `llm.voice` | 97 | 100% | macOS node | Moved there on the last day; 3 earlier requests ran on the primary |
| `llm.secondary` | 34 | 2.9% | Qualification host | 31 errors and 2 disconnects; see below |
| `vision.general` | 5 | 80% | Primary node | |
| `llm.auxiliary` | 2 | 100% | Primary node | |

Dispatched `llm.primary` requests by backing serve were 5,621, 2,005, 464, and
226, plus 2,229 with no serve identity recorded. Every dispatched request went
to its alias's one configured tier. Speech routes on the macOS node served 72
transcriptions and 21 syntheses.

## Volume and concurrency

- **Per day.** Chat averaged 362 requests per day: minimum 9, median 322, 90th
  percentile 599, maximum 974. Over the longer 49-day record, `llm.primary` ran
  from 19 to 1,295 requests per day with a median of 272.
- **Per hour.** 69% of clock hours had at least one chat request. In an active
  hour the median was 8 requests, the 90th percentile 69, and the maximum 234.
  The busiest single minute had 86 requests, all successful.
- **Time of day.** By six-hour block: 27.2% overnight (00–06), 35.2% morning
  (06–12), 16.3% afternoon, 21.3% evening. The 07:00 hour alone holds 18.9% of
  chat requests, a median of 81 per day from an owner-bound agent credential,
  which is the signature of a scheduled job.
- **Day of week.** Saturday through Tuesday each carry 17–18%; Wednesday through
  Friday each carry 9–11%.
- **Concurrency.** Measured from accept to end of each dispatched chat request:
  at least one request was in flight 2.9% of the time, two or more 0.26%, three
  or more 0.02%, four or more 0.006%. Of arriving requests, 88.5% found nothing
  else in flight, 9.1% found one, and 0.8% found four or more.
- **Peak.** The highest overlap was 20 requests, in one overnight burst. Five or
  more in flight occurred on only four days.

The engine's own gauges agree: over 14 days it had no running request in 96.9%
of samples, and a non-empty wait queue in 0.1%.

## Prompt and output lengths

`llm.primary`, 10,521 requests with measured tokens:

| | p10 | p50 | p90 | p95 | p99 | max | mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Input tokens | 2,135 | 28,907 | 169,581 | 205,889 | 237,541 | 316,040 | 59,573 |
| Output tokens | 33 | 284 | 3,566 | 7,190 | 17,068 | 36,478 | 1,446 |
| Latency (s) | 0.6 | 2.4 | 19.1 | 37.4 | 82.4 | 198.9 | 8.1 |

- Input size: 8.8% under 1k tokens, 25.7% 1–8k, 16.8% 8–32k, 31.9% 32–128k, and
  16.9% at 128k or more. Almost half of requests exceed 32k tokens.
- Output size: 30.7% under 100 tokens, 40.9% 100–1k, 19.6% 1–4k, 8.8% 4k or
  more. Reasoning tokens are 76% of output on average.
- `llm.voice` is the opposite shape: median 185 tokens in, 43 out, 0.54 s.

## Prefix and session repetition

Cache-read counts were reported for 10,332 of the 11,173 chat requests.

- **Token-weighted, 90.5% of input was served from cache** (556.6M of 614.8M).
  The engine's own counters put it at 94.3% over the last 14 days.
- **Per request:** 55.0% had at least 90% of their input cached, 17.6% had
  50–90%, 5.2% had under 50%, and 22.2% had none.
- **Real prefill work is small.** Uncached input per request has a median of
  1,700 tokens and a 90th percentile of 7,232. The 99th percentile is 142,987.
- **Cold large prompts are rare but costly.** Of 5,020 requests at 32k tokens or
  more, 91.3% were at least 90% cached and 3.8% were cold. That is about 190
  cold large prefills in 30 days, roughly six a day.
- **Requests arrive in tight runs.** On a given alias, 84.8% of requests follow
  the previous one within a minute and 98.8% within an hour.
- **Session-like chains, estimated.** Because no session identifier is recorded,
  chains were inferred from token counts alone: a request continues an earlier
  one when its cached prefix matches that request's size. By that estimate,
  66.9% of chains are a single request, 6.5% have five or more, and those long
  chains hold 57.1% of all requests. The longest has 224. Treat this as an
  estimate, not a count.
- **Agent credentials drive the reuse.** Owner-bound agent keys sent 7,302
  requests with a median prompt of 73,961 tokens and 92% cached. The extraction
  service's prompts were 34% cached.
- **The conversation cache is full.** The engine's prompt-state budget was at
  100% in every sample, holding 12.5 states on average and 21 at most. KV cache
  in use averaged 0.2% and peaked at 37%.
- **Speculation.** The engine's draft acceptance rate was 70.4% over 14 days.

## Queueing, errors, timeouts, fallbacks

- **Queueing.** The gateway did not record admission wait, so its own queue time
  is unknown. At the engine, arrival to admission took 6 ms at the median,
  0.28 s at the 95th percentile, and 0.64 s at the 99th. Time to first token was
  0.48 s, 3.5 s, and 14.5 s at the same points.
- **Rejections.** 524 chat requests were refused before dispatch, 490 of them
  on `llm.primary` (4.4% of its traffic), in a median of 13 ms. A closer look
  shows these are not intermittent. They fall into 21 windows in which no
  `llm.primary` request succeeded at all, and every one arrived with nothing
  else in flight. Host metrics for the windows inside the metrics retention
  show the primary's GPU memory dropping to near zero and the engine absent,
  with other work on the GPUs: the primary serve had been unloaded, on days
  when candidate models were being qualified. Callers on a schedule kept
  calling and were refused at once. The windows total at most 21.7 hours, 2.9%
  of the period. This is the cost of qualifying on the serving host, not a
  gateway defect.
- **Disconnects.** 130 `llm.primary` requests (1.2%) ended with the client
  disconnecting mid-stream, a median of 15 s in.
- **Clustering.** Three days hold 66% of all unsuccessful chat requests.
- **Timeouts.** None observed. No chat request ran five minutes; the longest was
  199 s. Two dispatched requests ended in a generation error.
- **Fallbacks.** None, by design at the time and in the data. When
  `llm.secondary` could not serve, its 31 callers received errors rather than another tier. An opt-in
  redirect for exactly this case was added to the gateway afterward and was not
  deployed during the measured period; see [ship-log.md](ship-log.md).

## Which hosts sit idle or saturated

Fourteen days of 5-minute samples.

| Host role | GPU busy (mean) | GPU idle (5% or less) | GPU at 80% or more | GPU memory in use | CPU busy (mean) | Inference load |
| --- | ---: | ---: | ---: | --- | ---: | --- |
| Primary node, two GPUs | 3.5% | 96.4% of samples | 3.5% | 90% or more in 99.6% of samples | 13% | 99.1% of dispatched chat |
| Qualification host, one GPU | 4.1% | 93.7% | 0.7% | 5% on average | 4% | 1 successful chat request in 30 days |
| macOS node | not exported | — | — | — | 13% | 94 voice chats, 72 transcriptions, 21 syntheses |

No host is saturated on compute. The primary's GPU memory is fully committed
because the model stays resident, while its compute sits idle. The qualification
host is idle and its inference endpoint was reachable in only 2.7% of samples,
which matches the 91% failure rate on `llm.secondary`. Traffic that reaches a
host without passing through the gateway is not visible here, and logs on the
other two hosts were not read.

## What this implies for the three picks

These are inferences from the numbers above, not decisions.

- **Session affinity** has nothing to choose between today. One engine serves
  98.8% of chat, and prefix reuse is already 90% without it. It becomes
  mandatory the moment agent traffic is split across more than one member or
  host: 73% of agent requests exceed 32k tokens and 91% of those depend on a
  warm prefix.
- **A larger conversation or KV cache in host memory** targets the one resource
  that is visibly full. The data cannot say how many of the roughly 190 cold
  large prefills were evictions rather than new sessions, so the first step is
  to measure that.
- **Load-aware speculation** has little load to react to. Two or more requests
  overlap 0.26% of the time, and only four days saw five or more in flight.
- **Cross-host capacity routing** would not relieve a capacity shortage, because
  there is none. What the data shows instead is a single-engine dependence and a
  secondary tier that is rarely up.

## Measurement gaps worth closing first

1. Record admission wait and time to first content in usage accounting; the
   fields exist and are empty.
2. Record a fixed error code on refused and failed requests.
3. Decide whether a privacy-safe session or prefix-affinity key should be
   counted, since session behavior can only be estimated today.
4. Attribute cold large prefills to eviction or to new sessions.
