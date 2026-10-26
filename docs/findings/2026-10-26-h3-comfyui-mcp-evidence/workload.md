# Workload and plan boundary

Functional integration smoke, concurrency one; not a serving comparison or a population quality study.
The pre-existing two-shot fictional cafe prompt and seed42 were reused unchanged through MCP.
The exact prompt is retained in [the cold request](mcp-cold.json). It requests visible synchronized speech,
a medium close-up followed by a wider period cafe, rotating fans, and no readable text/subtitles.
Prompt requests are not acceptance evidence.

Generation:864×480,124frames,24fps,20res_multistep/simple steps. Upscale:1728×960,
denoise0.1,two Euler steps,seed444,guide1,25000-token tiles,192overlap.
One base generation, one warm upscale and one cold upscale recheck completed. They are different
operations/revisions/cache states; no speedup, quantization fidelity or percentile claim is made.
The initial integration plan predates requests in the tracked implementation plan.
The cold retry followed the independently reviewed Windows publisher correction.

Three MP4s were exported, byte-length/SHA checked, fully decoded and probed for actual frame count.
A separate stream-copy finish retained the source compressed audio and refined video bitstreams.
Generated MP4s and sampled frames are held privately because output redistribution permission is unresolved.
The final file SHA256 is `53c5df27edc6e30ba36e6428a87c9d4bf4d8331a7c8e2262c9de96de7fa3fb6a`.
No recorded human podcast, likeness data, character LoRA or isolated host stems were used.
