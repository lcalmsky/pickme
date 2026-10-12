import { webcrypto as crypto } from "node:crypto";

const ITERATIONS = 200000;
const SALT_BYTES = 16;
const IV_BYTES = 12;

const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const plaintext = Buffer.concat(chunks);

const pin = process.argv[2];
if (!pin) {
  process.stderr.write("lock.mjs: missing PIN argument\n");
  process.exit(2);
}

const b64 = (view) => Buffer.from(view).toString("base64");
const random = (n) => crypto.getRandomValues(new Uint8Array(n));

const salt = random(SALT_BYTES);
const iv = random(IV_BYTES);

const keyMaterial = await crypto.subtle.importKey(
  "raw",
  new TextEncoder().encode(pin),
  "PBKDF2",
  false,
  ["deriveKey"],
);

const key = await crypto.subtle.deriveKey(
  { name: "PBKDF2", salt, iterations: ITERATIONS, hash: "SHA-256" },
  keyMaterial,
  { name: "AES-GCM", length: 256 },
  false,
  ["encrypt"],
);

const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plaintext);

process.stdout.write(
  JSON.stringify({
    salt: b64(salt),
    iv: b64(iv),
    ct: b64(ct),
    iterations: ITERATIONS,
  }),
);
