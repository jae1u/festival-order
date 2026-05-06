#!/usr/bin/env node

import { performance } from 'node:perf_hooks';

const ALLOWED_HOSTS = new Set([
  'localhost',
  '127.0.0.1',
  '::1',
  'csed-postech.n-e.kr',
]);

const PROD_HOST = 'csed-postech.n-e.kr';
const LOCAL_MAX = { rps: 25, concurrency: 20, durationSeconds: 120 };
const PROD_MAX = { rps: 5, concurrency: 5, durationSeconds: 60 };

function usage() {
  console.log(`Usage:
  npm run load:test -- --url https://localhost/ --duration 20 --rps 5 --concurrency 3
  npm run load:test -- --url https://csed-postech.n-e.kr/ --duration 30 --rps 2 --concurrency 2 --i-understand-prod

Only localhost, 127.0.0.1, ::1, and csed-postech.n-e.kr are allowed.
Defaults are intentionally small. This is a bounded availability smoke test, not a DoS tool.`);
}

function readArgs(argv) {
  const args = {
    method: 'GET',
    duration: 15,
    rps: 2,
    concurrency: 2,
    timeoutMs: 5000,
    prodAck: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      usage();
      process.exit(0);
    }
    if (arg === '--i-understand-prod') {
      args.prodAck = true;
      continue;
    }
    const next = argv[i + 1];
    if (!next) throw new Error(`Missing value for ${arg}`);
    if (arg === '--url') args.url = next;
    else if (arg === '--method') args.method = next.toUpperCase();
    else if (arg === '--duration') args.duration = Number(next);
    else if (arg === '--rps') args.rps = Number(next);
    else if (arg === '--concurrency') args.concurrency = Number(next);
    else if (arg === '--timeout-ms') args.timeoutMs = Number(next);
    else throw new Error(`Unknown argument: ${arg}`);
    i += 1;
  }

  return args;
}

function normalizeTarget(rawUrl) {
  if (!rawUrl) throw new Error('--url is required');

  const target = new URL(rawUrl);
  if (!['http:', 'https:'].includes(target.protocol)) {
    throw new Error('Only http:// and https:// URLs are allowed');
  }

  if (!ALLOWED_HOSTS.has(target.hostname)) {
    throw new Error(`Host is not allowlisted: ${target.hostname}`);
  }

  return target;
}

function enforceBounds(target, args) {
  if (!Number.isFinite(args.duration) || !Number.isFinite(args.rps) || !Number.isFinite(args.concurrency)) {
    throw new Error('duration, rps, and concurrency must be numbers');
  }
  if (args.duration <= 0 || args.rps <= 0 || args.concurrency <= 0) {
    throw new Error('duration, rps, and concurrency must be positive');
  }
  if (!['GET', 'HEAD'].includes(args.method)) {
    throw new Error('Only GET and HEAD are allowed for this script');
  }

  const isProd = target.hostname === PROD_HOST;
  const max = isProd ? PROD_MAX : LOCAL_MAX;

  if (isProd && !args.prodAck) {
    throw new Error(`Production target ${PROD_HOST} requires --i-understand-prod`);
  }
  if (args.rps > max.rps) throw new Error(`RPS limit for ${target.hostname} is ${max.rps}`);
  if (args.concurrency > max.concurrency) throw new Error(`Concurrency limit for ${target.hostname} is ${max.concurrency}`);
  if (args.duration > max.durationSeconds) {
    throw new Error(`Duration limit for ${target.hostname} is ${max.durationSeconds} seconds`);
  }
}

function percentile(values, p) {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[index];
}

async function main() {
  const args = readArgs(process.argv.slice(2));
  const target = normalizeTarget(args.url);
  enforceBounds(target, args);

  const stopAt = performance.now() + args.duration * 1000;
  const intervalMs = 1000 / args.rps;
  const inFlight = new Set();
  const latencies = [];
  const statuses = new Map();
  let sent = 0;
  let completed = 0;
  let failed = 0;

  console.log(`Target: ${target.toString()}`);
  console.log(`Method: ${args.method}, duration: ${args.duration}s, rps: ${args.rps}, concurrency: ${args.concurrency}`);
  if (target.protocol === 'https:' && ['localhost', '127.0.0.1', '::1'].includes(target.hostname)) {
    process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';
    console.log('Local HTTPS certificate verification is disabled for this process only.');
  }

  async function sendOne() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), args.timeoutMs);
    const started = performance.now();
    sent += 1;

    try {
      const response = await fetch(target, {
        method: args.method,
        redirect: 'manual',
        signal: controller.signal,
        headers: {
          'User-Agent': 'festival-order-bounded-load-test/1.0',
        },
      });
      statuses.set(response.status, (statuses.get(response.status) || 0) + 1);
      latencies.push(performance.now() - started);
      completed += 1;
    } catch {
      failed += 1;
    } finally {
      clearTimeout(timeout);
    }
  }

  while (performance.now() < stopAt) {
    if (inFlight.size < args.concurrency) {
      const task = sendOne().finally(() => inFlight.delete(task));
      inFlight.add(task);
    }
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  }

  await Promise.allSettled(inFlight);

  const statusSummary = [...statuses.entries()]
    .sort(([a], [b]) => a - b)
    .map(([status, count]) => `${status}:${count}`)
    .join(', ') || 'none';

  console.log('\nSummary');
  console.log(`Sent: ${sent}, completed: ${completed}, failed/timeouts: ${failed}`);
  console.log(`Statuses: ${statusSummary}`);
  console.log(`Latency ms p50=${percentile(latencies, 50).toFixed(1)} p95=${percentile(latencies, 95).toFixed(1)} max=${(Math.max(0, ...latencies)).toFixed(1)}`);
}

main().catch(error => {
  console.error(`Error: ${error.message}`);
  usage();
  process.exit(1);
});
