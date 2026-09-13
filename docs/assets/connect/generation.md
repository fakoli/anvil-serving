# Anvil Connect sign-in assets

Generated with the built-in imagegen tool on 2026-09-13. The original alpha PNG
is `anvil-mark-master.png`. The portal's `logo.png` and `favicon.ico` are resized
PNG and ICO exports of this same mark, preserving transparency. These are also
the source assets for the operator's Authelia `server.asset_path` directory.

`anvil-mark-nano-banana.png` is the requested Nano Banana Pro alternative,
generated using the same prompt with model alias `pro` (`gemini-3-pro-image`),
1:1 aspect and 1K size. Its checkerboard is baked into opaque RGB pixels, so it
is retained for comparison and is not used as the live transparent logo.

## Final prompt

```text
Use case: logo-brand
Asset type: production PNG logo for the Anvil Connect sign-in portal, also usable as a small browser favicon.
Primary request: create one clean, minimal Anvil brand symbol matching a developer infrastructure product. Depict a bold instantly recognizable blacksmith anvil silhouette: a wide horizontal top with a tapered horn at left and short squared heel at right, a narrow central waist, and a stable flared foot. Keep the geometry simple and crisp, appropriate at 48 pixels.
Style/medium: polished flat graphic, solid silhouette, restrained technical branding. No illustration scene, no photorealism, no 3D rendering.
Composition/framing: one centered compact symbol on a square canvas, generous but not excessive even transparent padding (about 12 percent); the symbol occupies around 76 percent of the canvas width. No enclosing tile or circle.
Color palette: primary saturated cyan #28c7d7, with only one small warm amber #f5ad35 inset accent at the waist. Intended to sit on dark navy #0b1118. Use genuine transparent background with alpha, not a navy/white background or a drawn checkerboard. The mark itself must stay visible on dark and light backgrounds.
Text: none. No letters, slogan, labels, watermark, gradients, glow, shadows, networks, gears, padlocks, or extra symbols.
Deliver one production-ready transparent raster logo, not a mockup or presentation sheet.
```
