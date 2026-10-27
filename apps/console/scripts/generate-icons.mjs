import { deflateSync } from 'node:zlib'
import { writeFile } from 'node:fs/promises'

const background = [10, 15, 13]
const tile = [15, 24, 19]
const border = [43, 65, 54]
const mint = [0, 245, 160]
const outerV = [[0.18, 0.22], [0.38, 0.22], [0.5, 0.59], [0.62, 0.22], [0.82, 0.22], [0.61, 0.8], [0.39, 0.8]]
const cut = [[0.46, 0.16], [0.54, 0.16], [0.54, 0.43], [0.46, 0.43]]

function inside(point, polygon) {
  const [x, y] = point
  let hit = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i]
    const [xj, yj] = polygon[j]
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit
  }
  return hit
}

function roundedTile(x, y, inset, radius) {
  const left = inset, right = 1 - inset, top = inset, bottom = 1 - inset
  if (x < left || x > right || y < top || y > bottom) return false
  const cx = Math.max(left + radius, Math.min(x, right - radius))
  const cy = Math.max(top + radius, Math.min(y, bottom - radius))
  return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2
}

function pixel(x, y) {
  if (inside([x, y], outerV) && !inside([x, y], cut)) return mint
  if (roundedTile(x, y, 0.035, 0.17)) {
    if (!roundedTile(x, y, 0.043, 0.16)) return border
    return tile
  }
  return background
}

function png(size) {
  const rows = Buffer.alloc((size * 4 + 1) * size)
  for (let y = 0; y < size; y++) {
    const row = y * (size * 4 + 1)
    rows[row] = 0
    for (let x = 0; x < size; x++) {
      const samples = [[0.25, 0.25], [0.75, 0.25], [0.25, 0.75], [0.75, 0.75]]
        .map(([dx, dy]) => pixel((x + dx) / size, (y + dy) / size))
      const offset = row + 1 + x * 4
      for (let channel = 0; channel < 3; channel++) {
        rows[offset + channel] = Math.round(samples.reduce((sum, color) => sum + color[channel], 0) / samples.length)
      }
      rows[offset + 3] = 255
    }
  }
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])
  const header = Buffer.alloc(13)
  header.writeUInt32BE(size, 0)
  header.writeUInt32BE(size, 4)
  header.set([8, 6, 0, 0, 0], 8)
  return Buffer.concat([signature, chunk('IHDR', header), chunk('IDAT', deflateSync(rows, { level: 9 })), chunk('IEND', Buffer.alloc(0))])
}

function chunk(type, data) {
  const name = Buffer.from(type)
  const length = Buffer.alloc(4)
  length.writeUInt32BE(data.length)
  const checksum = Buffer.alloc(4)
  checksum.writeUInt32BE(crc32(Buffer.concat([name, data])))
  return Buffer.concat([length, name, data, checksum])
}

function crc32(data) {
  let crc = 0xffffffff
  for (const byte of data) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0)
  }
  return (crc ^ 0xffffffff) >>> 0
}

for (const [name, size] of [['apple-touch-icon.png', 180], ['pwa-192.png', 192], ['pwa-512.png', 512], ['pwa-maskable-512.png', 512]]) {
  await writeFile(new URL(`../public/icons/${name}`, import.meta.url), png(size))
}
console.log('Generated 4 deterministic PWA icons')
