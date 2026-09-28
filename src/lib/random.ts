const HEX = '0123456789abcdef';

export const randomHex = (bytes: number) => {
  let out = '0x';
  for (let i = 0; i < bytes * 2; i++) out += HEX[Math.floor(Math.random() * 16)];
  return out;
};

export const randomAddress = () => randomHex(20);
export const randomTxHash = () => randomHex(32);

export const between = (min: number, max: number) => min + Math.random() * (max - min);
export const pick = <T,>(items: T[]): T => items[Math.floor(Math.random() * items.length)];

/** Deterministic 0..1 from a string (used to place nodes on the map) */
export function hashUnit(input: string, salt = 0) {
  let h = 2166136261 ^ salt;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
}
