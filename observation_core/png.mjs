import { inflateSync } from "node:zlib";

const MAX_ENCODED_BYTES = 8 * 1024 * 1024;
const MAX_PIXELS = 8_000_000;
const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const crcTable = Uint32Array.from({ length: 256 }, (_, value) => {
  let crc = value;
  for (let bit = 0; bit < 8; bit += 1) crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  return crc >>> 0;
});

export class PngError extends Error {
  constructor(code) { super(code); this.code = code; }
}

const fail = (code) => { throw new PngError(code); };
const base64Byte = (byte) => (byte >= 65 && byte <= 90) || (byte >= 97 && byte <= 122) || (byte >= 48 && byte <= 57) || byte === 43 || byte === 47;
const typeByte = (byte) => (byte >= 65 && byte <= 90) || (byte >= 97 && byte <= 122);
const crc32 = (bytes) => {
  let crc = 0xffffffff;
  for (const byte of bytes) crc = crcTable[(crc ^ byte) & 255] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
};

function canonicalBase64(value) {
  if (value.length % 4 !== 0) return false;
  const padding = value.endsWith("==") ? 2 : value.endsWith("=") ? 1 : 0;
  for (let index = 0; index < value.length - padding; index += 1) if (!base64Byte(value.charCodeAt(index))) return false;
  for (let index = value.length - padding; index < value.length; index += 1) if (value.charCodeAt(index) !== 61) return false;
  return true;
}

function limits(options) {
  if (options === undefined) return { maxEncodedBytes: MAX_ENCODED_BYTES, maxPixels: MAX_PIXELS };
  if (!options || typeof options !== "object" || Array.isArray(options) || Object.getPrototypeOf(options) !== Object.prototype || Object.keys(options).some((key) => key !== "maxEncodedBytes" && key !== "maxPixels")) fail("invalid_image");
  const maxEncodedBytes = Object.hasOwn(options, "maxEncodedBytes") ? options.maxEncodedBytes : MAX_ENCODED_BYTES;
  const maxPixels = Object.hasOwn(options, "maxPixels") ? options.maxPixels : MAX_PIXELS;
  if (!Number.isInteger(maxEncodedBytes) || maxEncodedBytes < 1 || maxEncodedBytes > MAX_ENCODED_BYTES || !Number.isInteger(maxPixels) || maxPixels < 1 || maxPixels > MAX_PIXELS) fail("invalid_image");
  return { maxEncodedBytes, maxPixels };
}

/** Validates a canonical-base64 PNG and returns its original compressed bytes. Only IHDR, IDAT, and IEND are admitted. */
export function validatePng(image, options) {
  const { maxEncodedBytes, maxPixels } = limits(options);
  if (typeof image !== "string" || image.length === 0 || image.length > 4 * Math.ceil(maxEncodedBytes / 3)) fail(image && typeof image === "string" ? "image_limit" : "invalid_image");
  if (!canonicalBase64(image)) fail("invalid_image");
  const png = Buffer.from(image, "base64");
  if (png.toString("base64") !== image) fail("invalid_image");
  if (png.length > maxEncodedBytes) fail("image_limit");
  if (png.length < SIGNATURE.length || !png.subarray(0, SIGNATURE.length).equals(SIGNATURE)) fail("invalid_image");

  let offset = SIGNATURE.length;
  let width, height, scanlineBytes;
  const idat = [];
  let stage = "ihdr";
  while (offset < png.length) {
    if (png.length - offset < 12) fail("invalid_image");
    const length = png.readUInt32BE(offset);
    if (length > png.length - offset - 12) fail("invalid_image");
    const typeStart = offset + 4;
    const dataStart = typeStart + 4;
    const dataEnd = dataStart + length;
    const typeBytes = png.subarray(typeStart, dataStart);
    if (![...typeBytes].every(typeByte) || typeBytes[2] < 65 || typeBytes[2] > 90) fail("invalid_image");
    const type = typeBytes.toString("ascii");
    if (crc32(png.subarray(typeStart, dataEnd)) !== png.readUInt32BE(dataEnd)) fail("invalid_image");

    if (stage === "ihdr") {
      if (type !== "IHDR" || length !== 13) fail(type === "IHDR" ? "invalid_image" : "unsupported_image");
      width = png.readUInt32BE(dataStart); height = png.readUInt32BE(dataStart + 4);
      const depth = png[dataStart + 8], color = png[dataStart + 9], compression = png[dataStart + 10], filter = png[dataStart + 11], interlace = png[dataStart + 12];
      if (depth !== 8 || (color !== 2 && color !== 6) || compression !== 0 || filter !== 0 || interlace !== 0) fail("unsupported_image");
      if (width === 0 || height === 0) fail("invalid_image");
      if (width > Math.floor(maxPixels / height)) fail("image_limit");
      const rowBytes = width * (color === 2 ? 3 : 4);
      scanlineBytes = (rowBytes + 1) * height;
      stage = "idat";
    } else if (stage === "idat") {
      if (type === "IDAT") idat.push(png.subarray(dataStart, dataEnd));
      else if (type === "IEND") {
        if (idat.length === 0 || length !== 0) fail("invalid_image");
        stage = "end";
      } else fail("unsupported_image");
    } else fail("invalid_image");
    offset = dataEnd + 4;
  }
  if (stage !== "end") fail("invalid_image");

  const compressed = Buffer.concat(idat);
  let scanlines;
  try {
    const inflated = inflateSync(compressed, { info: true, maxOutputLength: scanlineBytes });
    if (inflated.engine.bytesWritten !== compressed.length) fail("invalid_image");
    scanlines = inflated.buffer;
  } catch (error) { if (error instanceof PngError) throw error; fail("invalid_image"); }
  if (scanlines.length !== scanlineBytes) fail("invalid_image");
  for (let row = 0; row < height; row += 1) if (scanlines[row * (scanlineBytes / height)] > 4) fail("invalid_image");
  return Object.freeze({ png, width, height });
}
