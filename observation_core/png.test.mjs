import assert from "node:assert/strict";
import { deflateSync } from "node:zlib";
import test from "node:test";
import { PngError, validatePng } from "./png.mjs";

const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const crc32 = (bytes) => {
  let crc = 0xffffffff;
  for (const byte of bytes) { crc ^= byte; for (let bit = 0; bit < 8; bit += 1) crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1; }
  return (crc ^ 0xffffffff) >>> 0;
};
const chunk = (type, data = Buffer.alloc(0)) => {
  const typeBytes = typeof type === "string" ? Buffer.from(type) : type;
  const out = Buffer.alloc(data.length + 12); out.writeUInt32BE(data.length); typeBytes.copy(out, 4); data.copy(out, 8); out.writeUInt32BE(crc32(Buffer.concat([typeBytes, data])), data.length + 8); return out;
};
const png = ({ width = 1, height = 1, color = 2, depth = 8, compression = 0, filterMethod = 0, interlace = 0, scanlines, chunks } = {}) => {
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width); ihdr.writeUInt32BE(height, 4); ihdr.set([depth, color, compression, filterMethod, interlace], 8);
  const data = scanlines ?? Buffer.concat(Array.from({ length: height }, () => Buffer.alloc(1 + width * (color === 6 ? 4 : 3))));
  return Buffer.concat([signature, ...(chunks ?? [chunk("IHDR", ihdr), chunk("IDAT", deflateSync(data)), chunk("IEND")])]);
};
const encoded = (fixture) => fixture.toString("base64");
const rejects = (image, code, options) => assert.throws(() => validatePng(image, options), (error) => error instanceof PngError && error.code === code);

test("admits independently constructed RGB and RGBA PNGs without changing bytes", () => {
  for (const [color, width, height] of [[2, 2, 1], [6, 1, 2]]) {
    const fixture = png({ color, width, height }), actual = validatePng(encoded(fixture));
    assert.deepEqual(actual.png, fixture); assert.equal(actual.width, width); assert.equal(actual.height, height);
  }
});

test("rejects noncanonical input, bad signatures, truncation, CRCs, and chunk order", () => {
  const fixture = png(), valid = encoded(fixture);
  rejects(`${valid}\n`, "invalid_image"); rejects(encoded(Buffer.from("not-png")), "invalid_image"); rejects(encoded(fixture.subarray(0, -1)), "invalid_image");
  rejects("A".repeat(8 * 1024 * 1024), "invalid_image");
  const badCrc = Buffer.from(fixture); badCrc[badCrc.length - 5] ^= 1; rejects(encoded(badCrc), "invalid_image");
  const ihdr = fixture.subarray(8, 33), idat = fixture.subarray(33, -12), iend = fixture.subarray(-12);
  rejects(encoded(Buffer.concat([signature, idat, ihdr, iend])), "unsupported_image");
  rejects(encoded(Buffer.concat([signature, ihdr, idat, chunk("tEXt", Buffer.from("x")), iend])), "unsupported_image");
  rejects(encoded(Buffer.concat([signature, ihdr, chunk(Buffer.from([0xc9, 68, 65, 84]), deflateSync(Buffer.alloc(4))), iend])), "invalid_image");
  rejects(encoded(Buffer.concat([signature, ihdr, chunk("IDaT", deflateSync(Buffer.alloc(4))), iend])), "invalid_image");
  rejects(encoded(Buffer.concat([fixture, Buffer.from([0])])), "invalid_image");
  rejects(`${valid}AA`, "invalid_image");
});

test("rejects unsupported modes and image or encoded-size bounds", () => {
  rejects(encoded(png({ color: 3 })), "unsupported_image"); rejects(encoded(png({ depth: 16 })), "unsupported_image"); rejects(encoded(png({ interlace: 1 })), "unsupported_image");
  rejects(encoded(png({ width: 0 })), "invalid_image");
  rejects(encoded(png({ width: 2, height: 2 })), "image_limit", { maxPixels: 3 });
  rejects(encoded(png()), "image_limit", { maxEncodedBytes: 1 });
  rejects(encoded(png()), "invalid_image", { maxPixels: 8_000_001 });
  rejects(encoded(png()), "invalid_image", { maxPixels: null });
});

test("rejects streams that inflate too much or too little, and invalid filter bytes", () => {
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(1); ihdr.writeUInt32BE(1, 4); ihdr.set([8, 2, 0, 0, 0], 8);
  const withStream = (stream) => encoded(png({ chunks: [chunk("IHDR", ihdr), chunk("IDAT", stream), chunk("IEND")] }));
  rejects(withStream(deflateSync(Buffer.alloc(5))), "invalid_image");
  rejects(withStream(deflateSync(Buffer.alloc(7))), "invalid_image");
  rejects(withStream(deflateSync(Buffer.from([5, 0, 0, 0]))), "invalid_image");
  rejects(withStream(Buffer.concat([deflateSync(Buffer.alloc(4)), Buffer.from([0])])), "invalid_image");
});
