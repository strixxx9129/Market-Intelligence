// Write dist/_headers (Cloudflare Pages response headers) after `vite build`.
// The CSP's connect-src must name the API origin, which differs per environment
// (VITE_API_URL), so this file is generated at build time instead of committed.
import { writeFileSync } from 'node:fs';

const api = new URL(process.env.VITE_API_URL ?? 'http://localhost:8000').origin;

const csp = [
  "default-src 'self'",
  "script-src 'self'", // no inline scripts, no eval: an injected <script> won't run
  "style-src 'self' 'unsafe-inline'", // React style={{...}} attributes (widths of bars)
  "img-src 'self' data:",
  "font-src 'self'",
  `connect-src 'self' ${api}`, // the only server the page may talk to
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'", // can't be framed: no clickjacking
  'upgrade-insecure-requests',
].join('; ');

const headers = `/*
  Content-Security-Policy: ${csp}
  Strict-Transport-Security: max-age=31536000; includeSubDomains
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: strict-origin-when-cross-origin
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  Cross-Origin-Opener-Policy: same-origin

/assets/*
  Cache-Control: public, max-age=31536000, immutable

/index.html
  Cache-Control: no-cache
`;

writeFileSync('dist/_headers', headers);
console.log(`wrote dist/_headers (connect-src ${api})`);
