#!/usr/bin/env node
/**
 * FON Daily BI Report — toolkit
 *
 * Pulls live LemonSqueezy + Amplitude + Plain data and generates a
 * daily business intelligence report with metrics, trends, alerts,
 * and an MRR forecast.
 *
 * Usage:
 *   node bi-toolkit.js                          # JSON report to stdout
 *   node bi-toolkit.js --slack                  # Slack-formatted report
 *   node bi-toolkit.js --alerts-only            # Only fire if thresholds breached
 *
 * Required env vars:
 *   LEMONSQUEEZY_API_KEY
 *   AMPLITUDE_API_KEY
 *   AMPLITUDE_SECRET_KEY
 *   PLAIN_API_KEY (optional — Plain support is best-effort)
 */

const https = require('https');
const http = require('http');
const zlib = require('zlib');
const { Buffer } = require('buffer');

// ── Config ──────────────────────────────────────────────────────────────────────
const LS_API_BASE = 'https://api.lemonsqueezy.com/v1';
const AMPLITUDE_EXPORT_BASE = 'https://amplitude.com/api/2';
const AMPLITUDE_DASHBOARD_BASE = 'https://amplitude.com/api';
const PLAIN_API_BASE = 'https://core-api.uk.plain.com/graphql/v1';

const LS_API_KEY = process.env.LEMONSQUEEZY_API_KEY || '';
const AMPLITUDE_API_KEY = process.env.AMPLITUDE_API_KEY || '';
const AMPLITUDE_SECRET_KEY = process.env.AMPLITUDE_SECRET_KEY || '';
const PLAIN_API_KEY = process.env.PLAIN_API_KEY || '';
const SLACK_WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL || '';

// ── HTTP helpers ─────────────────────────────────────────────────────────────────
function fetch(url, options = {}) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    mod.get(url, options, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch { resolve({ raw: data.slice(0, 1000), parseError: true }); }
      });
    }).on('error', reject);
  });
}

function post(url, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    const bodyStr = JSON.stringify(body);
    const req = mod.request(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
    }, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try { resolve(JSON.parse(data)); }
        catch { resolve({ raw: data.slice(0, 1000), parseError: true }); }
      });
    });
    req.on('error', reject);
    req.write(bodyStr);
    req.end();
  });
}


/**
 * Fetch a binary response as a Buffer. Used for Amplitude export zip.
 */
function fetchBinary(url, options = {}, timeoutMs = 30000) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https') ? https : http;
    const req = mod.get(url, options, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve(Buffer.concat(chunks)));
    });
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error('Request timed out after ' + timeoutMs + 'ms'));
    });
    req.on('error', reject);
  });
}

// ── LemonSqueezy ─────────────────────────────────────────────────────────────────
async function fetchLSSubscriptions() {
  if (!LS_API_KEY) return { error: 'LEMONSQUEEZY_API_KEY not set' };

  const all = [];
  let page = 1;
  let lastPage = 1;

  while (page <= lastPage) {
    const url = LS_API_BASE + '/subscriptions?page[size]=100&page[number]=' + page;
    const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + LS_API_KEY, Accept: 'application/vnd.api+json' } });
    if (resp.error) return resp;
    if (resp.data) all.push(...resp.data);
    lastPage = resp.meta?.page?.lastPage || 1;
    page++;
  }
  return all;
}

async function fetchLSPrices() {
  if (!LS_API_KEY) return { error: 'LEMONSQUEEZY_API_KEY not set' };

  const all = [];
  let page = 1;
  let lastPage = 1;

  while (page <= lastPage) {
    const url = LS_API_BASE + '/prices?page[size]=100&page[number]=' + page;
    const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + LS_API_KEY, Accept: 'application/vnd.api+json' } });
    if (resp.error) return resp;
    if (resp.data) all.push(...resp.data);
    lastPage = resp.meta?.page?.lastPage || 1;
    page++;
  }
  return all;
}

async function fetchLSOrders() {
  if (!LS_API_KEY) return { error: 'LEMONSQUEEZY_API_KEY not set' };

  const all = [];
  let page = 1;
  let lastPage = 1;

  while (page <= lastPage) {
    const url = LS_API_BASE + '/orders?page[size]=100&page[number]=' + page;
    const resp = await fetch(url, { headers: { Authorization: 'Bearer ' + LS_API_KEY, Accept: 'application/vnd.api+json' } });
    if (resp.error) return resp;
    if (resp.data) all.push(...resp.data);
    lastPage = resp.meta?.page?.lastPage || 1;
    page++;
  }
  return all;
}

// ── Amplitude ────────────────────────────────────────────────────────────────────
// NOTE (2026-07-28): credentials are now correctly configured (agentos/fon/amplitude_api_key
// + agentos/fon/amplitude_secret_key — project-scoped to Font Replacer, do NOT reuse
// chrisabad/amplitude/api_key, that is the chrisabad.com portfolio's key).
//
// The Amplitude /2/export endpoint returns a zip of gzipped NDJSON event files.
// This implementation:
//   1. Fetches the zip as binary
//   2. Parses the stored-zip container (no compression at zip level — store method)
//   3. Gunzips each member file
//   4. Parses NDJSON lines, counting distinct user_ids per day for DAU
//   5. Returns a summary with DAU per day and total active users in the window

/**
 * Parse an Amplitude export zip: extract entries, gunzip, count DAU per day.
 * Returns an array of day-maps (each: { date: Set<user_id> }).
 */
async function parseAmplitudeZip(zipBuf) {
  const entries = parseStoredZip(zipBuf);
  const dayMaps = [];
  for (const entry of entries) {
    if (entry.error || !entry.buffer || entry.buffer.length === 0) continue;
    let ndjsonBuf;
    try {
      ndjsonBuf = zlib.gunzipSync(entry.buffer);
    } catch {
      ndjsonBuf = entry.buffer; // not gzipped
    }
    dayMaps.push(countDAUFromNDJSON(ndjsonBuf));
  }
  return dayMaps;
}

/**
 * Parse a stored (no-compression) zip buffer into an array of {name, buffer} entries.
 * Amplitude's export zip uses the 'store' method (0), so we don't need a full
 * inflate — just walk the local file headers.
 */
function parseStoredZip(buf) {
  const entries = [];
  let offset = 0;

  while (offset < buf.length - 30) {
    // Local file header signature: 0x04034b50
    if (buf.readUInt32LE(offset) !== 0x04034b50) break;

    const versionNeeded = buf.readUInt16LE(offset + 4);
    const flags = buf.readUInt16LE(offset + 6);
    const compression = buf.readUInt16LE(offset + 8);
    const crc32 = buf.readUInt32LE(offset + 14);
    const compressedSize = buf.readUInt32LE(offset + 18);
    const uncompressedSize = buf.readUInt32LE(offset + 22);
    const nameLen = buf.readUInt16LE(offset + 26);
    const extraLen = buf.readUInt16LE(offset + 28);

    const name = buf.slice(offset + 30, offset + 30 + nameLen).toString('utf-8');
    const dataStart = offset + 30 + nameLen + extraLen;
    let dataEnd;
    let ddIdx = -1;
    if (compressedSize > 0) {
      dataEnd = dataStart + compressedSize;
    } else {
      // Streaming zip: local header has 0 for sizes.
      // Check for data descriptor (PK\7\8) first, then next local header, then end.
      const ddSig = Buffer.from([0x50, 0x4b, 0x07, 0x08]);
      ddIdx = buf.indexOf(ddSig, dataStart);
      if (ddIdx >= 0 && ddIdx < dataStart + 100000) {
        dataEnd = ddIdx;
      } else {
        const lhSig = Buffer.from([0x50, 0x4b, 0x03, 0x04]);
        const nextIdx = buf.indexOf(lhSig, dataStart + 1);
        dataEnd = nextIdx >= 0 ? nextIdx : buf.length;
      }
    }

    if (compression === 0) {
      // Stored (no compression at zip level)
      entries.push({ name, buffer: buf.slice(dataStart, dataEnd) });
    } else if (compression === 8) {
      // Deflated — shouldn't happen for Amplitude but handle gracefully
      try {
        const dataLen = compressedSize > 0 ? compressedSize : (dataEnd - dataStart);
        const inflated = zlib.inflateRawSync(buf.slice(dataStart, dataStart + dataLen));
        entries.push({ name, buffer: inflated });
      } catch (e) {
        entries.push({ name, buffer: null, error: 'inflate failed: ' + e.message });
      }
    } else {
      entries.push({ name, buffer: null, error: 'unsupported compression method: ' + compression });
    }

    offset = dataEnd + (dataEnd === ddIdx ? 16 : 0);
  }

  return entries;
}

/**
 * Parse NDJSON lines from a buffer, counting distinct user_ids per day.
 * Returns a map of date -> Set of user_ids.
 */
function countDAUFromNDJSON(buf) {
  const byDay = {};
  const text = buf.toString('utf-8');
  const lines = text.split('\n').filter(l => l.trim());

  for (const line of lines) {
    try {
      const evt = JSON.parse(line);
      const userId = evt.user_id || evt.device_id || evt.event_id;
      if (!userId) continue;

      // Amplitude event time is epoch milliseconds
      const ts = evt.time || evt.event_time;
      if (!ts) continue;

      let date;
      if (typeof ts === 'number') {
        date = new Date(ts).toISOString().slice(0, 10);
      } else if (typeof ts === 'string' && ts.match(/^\d{4}-\d{2}-\d{2}/)) {
        date = ts.slice(0, 10);
      } else {
        const n = parseInt(ts, 10);
        date = new Date(isNaN(n) ? ts : n).toISOString().slice(0, 10);
      }
      if (!byDay[date]) byDay[date] = new Set();
      byDay[date].add(String(userId));
    } catch {
      // skip malformed lines
    }
  }

  return byDay;
}

async function fetchAmplitudeEvents(daysBack = 7) {
  if (!AMPLITUDE_API_KEY || !AMPLITUDE_SECRET_KEY) return { error: 'AMPLITUDE_API_KEY or AMPLITUDE_SECRET_KEY not set' };

  const auth = Buffer.from(AMPLITUDE_API_KEY + ':' + AMPLITUDE_SECRET_KEY).toString('base64');
  const authHeader = { Authorization: 'Basic ' + auth };

  // Step 1: Verify credentials with a cheap taxonomy probe
  const probe = await fetch(AMPLITUDE_DASHBOARD_BASE + '/2/taxonomy/event', { headers: authHeader });
  if (probe && probe.error) {
    return { error: 'Amplitude credentials invalid: ' + JSON.stringify(probe.error) };
  }
  if (!probe || probe.parseError) {
    return { error: 'Amplitude credential check returned an unexpected (non-JSON) response' };
  }

  // Step 2: Fetch export zip — day-by-day to avoid timeouts
  const now = new Date();
  const end = new Date(now.getTime() + 86400000); // +1 day for timezone safety
  const start = new Date(now.getTime() - daysBack * 86400000);

  const fmt = (d) => {
    const y = d.getUTCFullYear();
    const m = String(d.getUTCMonth() + 1).padStart(2, '0');
    const day = String(d.getUTCDate()).padStart(2, '0');
    const h = String(d.getUTCHours()).padStart(2, '0');
    return y + m + day + 'T' + h;
  };

  // Fetch day-by-day to avoid timeouts on large windows
  const allEvents = [];
  const dayStart = new Date(start);
  while (dayStart < end) {
    const dayEnd = new Date(dayStart.getTime() + 86400000);
    const exportUrl = AMPLITUDE_EXPORT_BASE + '/export?start=' + fmt(dayStart) + '&end=' + fmt(dayEnd);
    try {
      const zipBuf = await fetchBinary(exportUrl, { headers: authHeader }, 30000);
      if (zipBuf && zipBuf.length >= 4 && zipBuf.readUInt32LE(0) === 0x04034b50) {
        const events = await parseAmplitudeZip(zipBuf);
        allEvents.push(...events);
      }
    } catch (err) {
      // Skip this day if it fails — partial data is better than none
    }
    dayStart.setTime(dayStart.getTime() + 86400000);
  }

  if (allEvents.length === 0) {
    return { error: 'Amplitude export returned no events across ' + daysBack + ' days' };
  }

  // Merge all day-maps into one
  const allByDay = {};
  let totalEvents = 0;
  let filesParsed = 0;
  let parseErrors = 0;
  for (const dayMap of allEvents) {
    for (const [date, users] of Object.entries(dayMap)) {
      if (!allByDay[date]) allByDay[date] = new Set();
      for (const uid of users) allByDay[date].add(uid);
      totalEvents += users.size;
    }
    filesParsed++;
  }

  // Build summary
  const sortedDates = Object.keys(allByDay).sort();
  const dauByDay = sortedDates.map(date => ({
    date,
    activeUsers: allByDay[date].size,
  }));

  // Total unique users across the entire window
  const allUsers = new Set();
  for (const users of Object.values(allByDay)) {
    for (const uid of users) allUsers.add(uid);
  }

  // Recent DAU (last 7 days average)
  const last7 = sortedDates.filter(d => {
    const ms = new Date(d).getTime();
    return ms >= now.getTime() - 7 * 86400000;
  });
  const avgDAU7 = last7.length > 0
    ? Math.round(last7.reduce((sum, d) => sum + allByDay[d].size, 0) / last7.length)
    : 0;

  // Today's DAU
  const todayStr = now.toISOString().slice(0, 10);
  const todayDAU = allByDay[todayStr]?.size || 0;

  // Yesterday's DAU
  const yesterdayStr = new Date(now.getTime() - 86400000).toISOString().slice(0, 10);
  const yesterdayDAU = allByDay[yesterdayStr]?.size || 0;

  return {
    status: 'available',
    totalUniqueUsers: allUsers.size,
    totalEvents,
    dauToday: todayDAU,
    dauYesterday: yesterdayDAU,
    dau7DayAvg: avgDAU7,
    dauByDay: dauByDay.slice(-30), // last 30 days
    daysInWindow: sortedDates.length,
    filesParsed,
    parseErrors,
  };
}
// ── Plain ────────────────────────────────────────────────────────────────────────
async function fetchPlainTimeline() {
  if (!PLAIN_API_KEY) return { error: 'PLAIN_API_KEY not set' };

  // Step 1: Fetch recent customers
  const customersQuery = {
    query: `{
      customers(first: 20) {
        edges {
          node {
            id
            fullName
            email { email }
            status
            createdAt { iso8601 }
          }
        }
      }
    }`,
  };

  let customers;
  try {
    customers = await post(PLAIN_API_BASE, customersQuery, { Authorization: 'Bearer ' + PLAIN_API_KEY });
  } catch (err) {
    return { error: 'Plain API unreachable: ' + err.message };
  }

  if (!customers || customers.errors || !customers.data?.customers?.edges) {
    return { error: 'Plain customers query failed: ' + JSON.stringify(customers?.errors || 'no data') };
  }

  const customerList = customers.data.customers.edges.map(e => e.node);
  const activeCustomers = customerList.filter(c => c.status === 'ACTIVE');

  // Step 2: Fetch timeline entries for each active customer (last 7 days)
  const now = new Date();
  const sevenDaysAgo = new Date(now.getTime() - 7 * 86400000).toISOString();

  const timelinePromises = activeCustomers.slice(0, 10).map(async (customer) => {
    const tlQuery = {
      query: `{
        timelineEntries(customerId: "${customer.id}", first: 5) {
          edges {
            node {
              id
              timestamp { iso8601 }
              entry {
                __typename
                ... on NoteEntry { noteText: text }
                ... on ChatEntry { chatText: text }
                ... on EmailEntry { subject }
                ... on CustomerEventEntry { __typename }
                ... on ThreadStatusTransitionedEntry { __typename }
              }
            }
          }
        }
      }`,
    };

    try {
      const result = await post(PLAIN_API_BASE, tlQuery, { Authorization: 'Bearer ' + PLAIN_API_KEY });
      const entries = result?.data?.timelineEntries?.edges?.map(e => ({
        id: e.node.id,
        timestamp: e.node.timestamp?.iso8601,
        type: e.node.entry?.__typename,
        subject: e.node.entry?.subject || null,
        text: e.node.entry?.noteText || e.node.entry?.chatText || null,
      })) || [];
      return { customer: customer.fullName, customerId: customer.id, email: customer.email?.email, entries };
    } catch {
      return { customer: customer.fullName, customerId: customer.id, email: customer.email?.email, entries: [], error: 'timeline fetch failed' };
    }
  });

  const timelines = await Promise.all(timelinePromises);

  // Step 3: Aggregate stats
  const totalEntries = timelines.reduce((sum, t) => sum + t.entries.length, 0);
  const entryTypes = {};
  for (const t of timelines) {
    for (const e of t.entries) {
      entryTypes[e.type] = (entryTypes[e.type] || 0) + 1;
    }
  }

  return {
    status: 'available',
    totalCustomers: customerList.length,
    activeCustomers: activeCustomers.length,
    customersWithActivity: timelines.filter(t => t.entries.length > 0).length,
    totalRecentEntries: totalEntries,
    entryTypeBreakdown: entryTypes,
    recentActivity: timelines.filter(t => t.entries.length > 0).slice(0, 5).map(t => ({
      customer: t.customer,
      email: t.email,
      recentEntryCount: t.entries.length,
      latestEntry: t.entries[0] || null,
    })),
  };
}

// ── Analysis ─────────────────────────────────────────────────────────────────────
function analyzeSubscriptions(subscriptions, priceMap) {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const yesterday = new Date(now.getTime() - 86400000).toISOString().slice(0, 10);
  const lastWeek = new Date(now.getTime() - 7 * 86400000).toISOString().slice(0, 10);
  const lastMonth = new Date(now.getTime() - 30 * 86400000).toISOString().slice(0, 10);

  const byDay = {};
  const byPlan = {};
  let active = 0, cancelled = 0, totalMRR = 0;
  let activeAtStart = 0, lifetimeChurnRate = 0;
  let todayNew = 0, todayCancelled = 0;
  let yesterdayNew = 0, yesterdayCancelled = 0;
  let lastWeekNew = 0, lastWeekCancelled = 0;
  let lastMonthNew = 0, lastMonthCancelled = 0;

  for (const sub of subscriptions) {
    const attrs = sub.attributes || {};
    const status = attrs.status || 'unknown';
    const createdAt = (attrs.created_at || '').slice(0, 10);
    const cancelledAt = (attrs.cancelled_at || '').slice(0, 10);
    const plan = attrs.product_name || 'Unknown';

    // Look up price from priceMap using first_subscription_item.price_id
    const priceId = attrs.first_subscription_item?.price_id;
    const priceInfo = priceMap[priceId] || {};
    const unitPriceCents = priceInfo.unit_price || 0;
    const intervalUnit = priceInfo.renewal_interval_unit || 'year';
    const intervalQty = priceInfo.renewal_interval_quantity || 1;

    // Convert to monthly MRR
    let monthlyMRR = 0;
    if (intervalUnit === 'year') {
      monthlyMRR = (unitPriceCents / 100) / (intervalQty * 12);
    } else if (intervalUnit === 'month') {
      monthlyMRR = (unitPriceCents / 100) / intervalQty;
    } else {
      monthlyMRR = (unitPriceCents / 100) / 12; // default to annual
    }

    if (status === 'active') {
      active++;
      totalMRR += monthlyMRR;
      // Active at start of period: created before lastMonth and still active
      if (createdAt && createdAt < lastMonth) activeAtStart++;
    }
    if (status === 'cancelled' || status === 'expired') {
      cancelled++;
    }

    // Plan breakdown
    if (!byPlan[plan]) byPlan[plan] = { active: 0, cancelled: 0, mrr: 0 };
    if (status === 'active') {
      byPlan[plan].active++;
      byPlan[plan].mrr += monthlyMRR;
    } else if (status === 'cancelled' || status === 'expired') {
      byPlan[plan].cancelled++;
    }

    // Daily counts
    if (createdAt) {
      if (createdAt === today) todayNew++;
      if (createdAt === yesterday) yesterdayNew++;
      if (createdAt >= lastWeek && createdAt < today) lastWeekNew++;
      if (createdAt >= lastMonth && createdAt < today) lastMonthNew++;
    }
    if (cancelledAt) {
      if (cancelledAt === today) todayCancelled++;
      if (cancelledAt === yesterday) yesterdayCancelled++;
      if (cancelledAt >= lastWeek && cancelledAt < today) lastWeekCancelled++;
      if (cancelledAt >= lastMonth && cancelledAt < today) lastMonthCancelled++;
    }
  }

  const lifetimeChurnCalc = active + cancelled > 0 ? cancelled / (active + cancelled) : 0;
  lifetimeChurnRate = lifetimeChurnCalc;
  const periodChurnRate = activeAtStart > 0 ? (lastMonthCancelled / activeAtStart) : 0;
  const arr = totalMRR * 12;

  return {
    totalSubscriptions: subscriptions.length,
    active,
    cancelled,
    churnRate: parseFloat((periodChurnRate * 100).toFixed(2)),
    lifetimeChurnRate: parseFloat((lifetimeChurnRate * 100).toFixed(2)),
    mrr: parseFloat(totalMRR.toFixed(2)),
    arr: parseFloat(arr.toFixed(2)),
    byPlan: Object.entries(byPlan).map(([name, stats]) => ({ name, ...stats })),
    daily: {
      today: { new: todayNew, cancelled: todayCancelled },
      yesterday: { new: yesterdayNew, cancelled: yesterdayCancelled },
      lastWeek: { new: lastWeekNew, cancelled: lastWeekCancelled },
      lastMonth: { new: lastMonthNew, cancelled: lastMonthCancelled },
    },
  };
}

function analyzeOrders(orders) {
  const now = new Date();
  const today = now.toISOString().slice(0, 10);
  const yesterday = new Date(now.getTime() - 86400000).toISOString().slice(0, 10);
  const dayBefore = new Date(now.getTime() - 2 * 86400000).toISOString().slice(0, 10);
  const lastWeek = new Date(now.getTime() - 7 * 86400000).toISOString().slice(0, 10);

  let todayRevenue = 0, yesterdayRevenue = 0, dayBeforeRevenue = 0, lastWeekRevenue = 0;
  let todayOrders = 0, yesterdayOrders = 0, dayBeforeOrders = 0;
  let paymentFailuresToday = 0, paymentFailuresYesterday = 0;
  let totalOrdersToday = 0;

  for (const order of orders) {
    const attrs = order.attributes || {};
    const date = (attrs.created_at || '').slice(0, 10);
    const total = parseFloat(attrs.total || 0) / 100;
    const status = attrs.status || '';

    if (date === today) {
      todayRevenue += total;
      todayOrders++;
      totalOrdersToday++;
      if (status === 'failed' || status === 'refunded') paymentFailuresToday++;
    }
    if (date === yesterday) {
      yesterdayRevenue += total;
      yesterdayOrders++;
      if (status === 'failed' || status === 'refunded') paymentFailuresYesterday++;
    }
    if (date === dayBefore) {
      dayBeforeRevenue += total;
      dayBeforeOrders++;
    }
    if (date >= lastWeek && date < today) {
      lastWeekRevenue += total;
    }
  }

  return {
    today: { revenue: parseFloat(todayRevenue.toFixed(2)), orders: todayOrders, paymentFailures: paymentFailuresToday },
    yesterday: { revenue: parseFloat(yesterdayRevenue.toFixed(2)), orders: yesterdayOrders, paymentFailures: paymentFailuresYesterday },
    dayBefore: { revenue: parseFloat(dayBeforeRevenue.toFixed(2)), orders: dayBeforeOrders },
    lastWeekRevenue: parseFloat(lastWeekRevenue.toFixed(2)),
    paymentFailureRate: totalOrdersToday > 0 ? parseFloat(((paymentFailuresToday / totalOrdersToday) * 100).toFixed(1)) : 0,
  };
}

// ── Alerts ───────────────────────────────────────────────────────────────────────
function checkAlerts(subAnalysis, orderAnalysis) {
  const alerts = [];

  // MRR decline: >5% day-over-day (compare complete periods: yesterday vs day-before-yesterday)
  const mrrChange = orderAnalysis.dayBefore.revenue > 0
    ? ((orderAnalysis.yesterday.revenue - orderAnalysis.dayBefore.revenue) / orderAnalysis.dayBefore.revenue) * 100
    : 0;
  if (mrrChange < -5) {
    alerts.push({ severity: 'critical', metric: 'MRR decline', message: 'MRR dropped ' + Math.abs(mrrChange).toFixed(1) + '% day-over-day (threshold: >5%) — yesterday vs day-before' });
  }
  const netChange = orderAnalysis.yesterday.revenue - orderAnalysis.dayBefore.revenue;
  if (netChange < -100) {
    alerts.push({ severity: 'warning', metric: 'Net-negative revenue', message: 'Net-negative movement of $' + Math.abs(netChange).toFixed(2) + ' in a single day (threshold: >$100) — yesterday vs day-before' });
  }

  // Churn spike: >=3x trailing-7-day daily average, or >=5 cancellations in one day
  const avgDailyCancellations = subAnalysis.daily.lastWeek.cancelled / 7;
  if (avgDailyCancellations > 0 && subAnalysis.daily.today.cancelled >= avgDailyCancellations * 3) {
    alerts.push({ severity: 'critical', metric: 'Churn spike', message: 'Today\'s cancellations (' + subAnalysis.daily.today.cancelled + ') are ' + (subAnalysis.daily.today.cancelled / avgDailyCancellations).toFixed(1) + 'x the 7-day daily average (' + avgDailyCancellations.toFixed(1) + ')' });
  }
  if (subAnalysis.daily.today.cancelled >= 5) {
    alerts.push({ severity: 'warning', metric: 'High cancellations', message: subAnalysis.daily.today.cancelled + ' cancellations today (threshold: >=5)' });
  }

  // Payment failures: >=3 in a day, or >10% failure rate
  if (orderAnalysis.today.paymentFailures >= 3) {
    alerts.push({ severity: 'warning', metric: 'Payment failures', message: orderAnalysis.today.paymentFailures + ' payment failures today (threshold: >=3)' });
  }
  if (orderAnalysis.paymentFailureRate > 10) {
    alerts.push({ severity: 'critical', metric: 'High failure rate', message: 'Payment failure rate at ' + orderAnalysis.paymentFailureRate + '% (threshold: >10%)' });
  }

  return alerts;
}

// ── MRR Forecast ─────────────────────────────────────────────────────────────────
function forecastMRR(subAnalysis, orderAnalysis) {
  const mrr = subAnalysis.mrr;
  const dailyNet = orderAnalysis.yesterday.revenue - orderAnalysis.dayBefore.revenue;
  const dailyNetAvg = orderAnalysis.lastWeekRevenue / 7;

  // Simple trend-based projection
  const dailyTrend = dailyNetAvg || dailyNet || 0;
  const monthlyTrend = dailyTrend * 30;

  return {
    currentMRR: mrr,
    forecast30d: parseFloat((mrr + monthlyTrend).toFixed(2)),
    forecast60d: parseFloat((mrr + monthlyTrend * 2).toFixed(2)),
    forecast90d: parseFloat((mrr + monthlyTrend * 3).toFixed(2)),
    dailyNetAverage: parseFloat(dailyTrend.toFixed(2)),
    monthlyNetTrend: parseFloat(monthlyTrend.toFixed(2)),
  };
}

// ── Recommendations ──────────────────────────────────────────────────────────────
function generateRecommendations(subAnalysis, orderAnalysis, alerts) {
  const recs = [];
  const now = new Date();
  const todayStr = now.toISOString().slice(0, 10);
  const yesterdayStr = new Date(now.getTime() - 86400000).toISOString().slice(0, 10);

  // 1. Churn — name the specific plan or cohort
  const churnByPlan = subAnalysis.byPlan || [];
  const highestChurnPlan = churnByPlan
    .filter(p => p.cancelled > 0 && p.active > 0)
    .sort((a, b) => (b.cancelled / (b.active + b.cancelled)) - (a.cancelled / (a.active + a.cancelled)))[0];

  if (subAnalysis.churnRate > 5) {
    const planDetail = highestChurnPlan
      ? ` Plan "${highestChurnPlan.name}" has ${highestChurnPlan.cancelled} cancellations vs ${highestChurnPlan.active} active (${(highestChurnPlan.cancelled / (highestChurnPlan.active + highestChurnPlan.cancelled) * 100).toFixed(1)}% churn rate).`
      : '';
    recs.push({
      priority: 'high',
      action: `Churn at ${subAnalysis.churnRate}% — audit ${highestChurnPlan ? highestChurnPlan.name + ' plan' : 'cancellation reasons'}`,
      detail: `Overall churn rate is ${subAnalysis.churnRate}%.${planDetail} Pull the last 5 cancellation reasons from Plain and check if there's a common theme (pricing, missing feature, onboarding). If pricing-driven, consider a win-back offer or usage-based downgrade path.`
    });
  } else if (subAnalysis.churnRate > 3) {
    recs.push({
      priority: 'medium',
      action: `Churn at ${subAnalysis.churnRate}% — watch ${highestChurnPlan ? highestChurnPlan.name : 'top churn plan'}`,
      detail: `Churn is ${subAnalysis.churnRate}% — not critical but above healthy. ${highestChurnPlan ? `Plan "${highestChurnPlan.name}" has ${highestChurnPlan.cancelled} cancellations.` : ''} Set a weekly check on cancellation reasons. If it hits 5%, escalate to a retention campaign.`
    });
  }

  // 2. New subscriptions — specific action, not "consider promotional push"
  const weeklyAvg = subAnalysis.daily.lastWeek.new / 7;
  const todayNew = subAnalysis.daily.today.new;
  if (todayNew < weeklyAvg * 0.7 && weeklyAvg > 0) {
    recs.push({
      priority: 'medium',
      action: `New subs today (${todayNew}) are ${weeklyAvg > 0 ? ((1 - todayNew / weeklyAvg) * 100).toFixed(0) : 0}% below weekly avg (${weeklyAvg.toFixed(1)}/day)`,
      detail: `Today's ${todayNew} new subscriptions is well below the 7-day daily average of ${weeklyAvg.toFixed(1)}. Check: (1) Is the Figma listing still live? (2) Did a promo code expire? (3) Any recent pricing page changes? If nothing changed externally, this may be normal weekend/weekday variance — compare to same day last week.`
    });
  }

  // 3. Payment failures — name the dollar amount at risk
  if (orderAnalysis.today.paymentFailures > 0) {
    const failedAmount = orderAnalysis.today.paymentFailures * (subAnalysis.mrr / subAnalysis.active);
    recs.push({
      priority: 'high',
      action: `${orderAnalysis.today.paymentFailures} payment failure(s) today — ~$${failedAmount.toFixed(0)} MRR at risk`,
      detail: `${orderAnalysis.today.paymentFailures} payment(s) failed today. Each failed payment risks churn. Action: (1) Check if it's a specific card processor issue. (2) LemonSqueezy auto-retries — verify retry schedule. (3) If same customer has >2 failures, reach out proactively with an updated payment link.`
    });
  }

  // 4. Net growth — specific number and what to do
  const netMonth = subAnalysis.daily.lastMonth.new - subAnalysis.daily.lastMonth.cancelled;
  if (netMonth > 5) {
    recs.push({
      priority: 'low',
      action: `Net +${netMonth} subscribers this month — double down on what's working`,
      detail: `You added ${subAnalysis.daily.lastMonth.new} new and lost ${subAnalysis.daily.lastMonth.cancelled} cancelled this month (net +${netMonth}). Check which acquisition channel drove the most new subs in the last 30 days. If it's organic (Figma listing), consider a featured listing request. If it's referral, run a "refer a friend" campaign.`
    });
  } else if (netMonth < -5) {
    recs.push({
      priority: 'high',
      action: `Net -${Math.abs(netMonth)} subscribers this month — urgent retention review`,
      detail: `You're losing ${Math.abs(netMonth)} more subscribers than you're gaining this month. Immediate actions: (1) Review the last 10 cancellations in Plain for common themes. (2) Check if a competitor launched or pricing changed. (3) Consider a 30-day retention email sequence for at-risk users.`
    });
  }

  // 5. Revenue day-over-day — specific dollar amounts
  if (orderAnalysis.yesterday.revenue > 0 && orderAnalysis.dayBefore.revenue > 0) {
    const change = ((orderAnalysis.yesterday.revenue - orderAnalysis.dayBefore.revenue) / orderAnalysis.dayBefore.revenue * 100);
    const absChange = Math.abs(change);
    if (absChange > 10) {
      const direction = change > 0 ? 'up' : 'down';
      const severity = change > 0 ? 'low' : 'medium';
      recs.push({
        priority: severity,
        action: `Revenue ${direction} ${absChange.toFixed(1)}% day-over-day ($${orderAnalysis.yesterday.revenue.toFixed(2)} vs $${orderAnalysis.dayBefore.revenue.toFixed(2)})`,
        detail: change > 0
          ? `Yesterday's revenue ($${orderAnalysis.yesterday.revenue.toFixed(2)}) was ${absChange.toFixed(1)}% above the day before. Check if this was a one-off (annual plan upgrade, bulk purchase) or a genuine trend shift. If a trend, identify the source and consider increasing that channel's budget.`
          : `Yesterday's revenue ($${orderAnalysis.yesterday.revenue.toFixed(2)}) dropped ${absChange.toFixed(1)}% from the day before ($${orderAnalysis.dayBefore.revenue.toFixed(2)}). Check: (1) Any failed payments yesterday? (2) Weekend effect? (3) Refund or chargeback? If no clear cause, monitor for 48h before acting.`
      });
    }
  }

  // 6. MRR health — specific dollar impact
  if (subAnalysis.mrr > 0 && subAnalysis.active > 0) {
    const avgRevenuePerSub = subAnalysis.mrr / subAnalysis.active;
    const mrrFromNew = subAnalysis.daily.lastMonth.new * avgRevenuePerSub;
    const mrrLost = subAnalysis.daily.lastMonth.cancelled * avgRevenuePerSub;
    const mrrChange = mrrFromNew - mrrLost;
    if (Math.abs(mrrChange) > 50) {
      const direction = mrrChange > 0 ? 'gaining' : 'losing';
      recs.push({
        priority: mrrChange > 0 ? 'medium' : 'high',
        action: `MRR ${direction} ~$${Math.abs(mrrChange).toFixed(0)}/mo this month (new: +$${mrrFromNew.toFixed(0)}, churn: -$${mrrLost.toFixed(0)})`,
        detail: mrrChange > 0
          ? `New subscriptions this month add ~$${mrrFromNew.toFixed(0)} MRR while churn costs ~$${mrrLost.toFixed(0)}. Net +$${mrrChange.toFixed(0)}. To accelerate: (1) Push the plan with the highest conversion rate. (2) Reduce churn by ${(subAnalysis.daily.lastMonth.cancelled * 0.2).toFixed(0)} subs (20% reduction) to add ~$${(subAnalysis.daily.lastMonth.cancelled * 0.2 * avgRevenuePerSub).toFixed(0)} more MRR.`
          : `Churn is costing ~$${mrrLost.toFixed(0)} MRR this month vs ~$${mrrFromNew.toFixed(0)} from new subs. Net -$${Math.abs(mrrChange).toFixed(0)}. To reverse: (1) Reduce churn by just ${(subAnalysis.daily.lastMonth.cancelled * 0.3).toFixed(0)} subs (30% reduction) to save ~$${(subAnalysis.daily.lastMonth.cancelled * 0.3 * avgRevenuePerSub).toFixed(0)} MRR. (2) Increase new subs by ${(subAnalysis.daily.lastMonth.new * 0.2).toFixed(0)} (20% lift) to add ~$${(subAnalysis.daily.lastMonth.new * 0.2 * avgRevenuePerSub).toFixed(0)} MRR.`
      });
    }
  }

  // 7. Fallback — never "no action required"
  if (recs.length === 0) {
    recs.push({
      priority: 'low',
      action: 'All metrics stable — focus on growth levers',
      detail: `MRR is $${subAnalysis.mrr.toFixed(2)} with ${subAnalysis.active} active subs and ${subAnalysis.churnRate}% churn. No anomalies detected. Recommended focus: (1) Review the top 3 performing plans and consider a limited-time offer. (2) Check Plain for any unresolved support threads that could become churn risks. (3) Verify the Figma listing is up to date.`
    });
  }

  // Always return exactly 3, never filler
  while (recs.length < 3) {
    const avgRevenuePerSub = subAnalysis.active > 0 ? subAnalysis.mrr / subAnalysis.active : 0;
    recs.push({
      priority: 'low',
      action: `Maintain momentum — ${subAnalysis.active} active subs at $${subAnalysis.mrr.toFixed(2)} MRR`,
      detail: `Current run rate: $${subAnalysis.mrr.toFixed(2)} MRR from ${subAnalysis.active} subs (avg $${avgRevenuePerSub.toFixed(2)}/sub). To grow MRR by 10%, add ${Math.ceil(subAnalysis.mrr * 0.1 / avgRevenuePerSub)} new subs or reduce churn by ${Math.ceil(subAnalysis.active * 0.02)} subs. Recommended: A/B test the pricing page CTA this week.`
    });
  }

  return recs.slice(0, 3);
}

// ── Shared comparison utility (complete periods only) ──────────────────────────
/**
 * Compare two complete periods. Never compares a partial period (today) against
 * a complete one. Use this everywhere that needs day-over-day or period-over-period
 * comparisons.
 *
 * @param {Object} orderAnalysis - The order analysis object
 * @param {string} recentPeriod - 'yesterday' (default) or 'dayBefore'
 * @param {string} priorPeriod - 'dayBefore' (default) or 'dayBefore' for 2-day-back
 * @returns {{ revenueChange: number|null, ordersChange: number|null }}
 */
function compareCompletePeriods(orderAnalysis, recentPeriod, priorPeriod) {
  const rp = recentPeriod || 'yesterday';
  const pp = priorPeriod || 'dayBefore';
  const recent = orderAnalysis[rp] || {};
  const prior = orderAnalysis[pp] || {};
  return {
    revenueChange: prior.revenue > 0
      ? parseFloat((((recent.revenue - prior.revenue) / prior.revenue) * 100).toFixed(1))
      : null,
    ordersChange: prior.orders > 0
      ? parseFloat((((recent.orders - prior.orders) / prior.orders) * 100).toFixed(1))
      : null,
  };
}

// ── Report generation ────────────────────────────────────────────────────────────
function generateReport(subAnalysis, orderAnalysis, alerts, forecast, recs, amplitudeData, plainData) {
  return {
    generatedAt: new Date().toISOString(),
    period: { label: 'Daily', date: new Date().toISOString().slice(0, 10) },
    executiveSummary: {
      mrr: subAnalysis.mrr,
      arr: subAnalysis.arr,
      activeSubscriptions: subAnalysis.active,
      churnRate: subAnalysis.churnRate,
      newSubscriptionsToday: subAnalysis.daily.today.new,
      cancellationsToday: subAnalysis.daily.today.cancelled,
      revenueToday: orderAnalysis.today.revenue,
      alertCount: alerts.length,
    },
    metrics: {
      lemonSqueezy: {
        subscriptions: subAnalysis,
        orders: orderAnalysis,
      },
      amplitude: amplitudeData?.error ? { status: 'unavailable', error: amplitudeData.error } : { status: 'available', data: amplitudeData },
      plain: plainData?.error ? { status: 'unavailable', error: plainData.error } : { status: 'available', data: plainData },
    },
    comparisons: {
      vsYesterday: compareCompletePeriods(orderAnalysis, 'yesterday', 'dayBefore'),
      vsLastWeek: {
        revenueChange: orderAnalysis.lastWeekRevenue > 0
          ? parseFloat((((orderAnalysis.yesterday.revenue - orderAnalysis.lastWeekRevenue / 7) / (orderAnalysis.lastWeekRevenue / 7)) * 100).toFixed(1))
          : null,
      },
    },
    forecast,
    alerts,
    recommendations: recs,
  };
}

// ── Slack formatting ─────────────────────────────────────────────────────────────
function formatSlackReport(report) {
  const s = report.executiveSummary;
  const blocks = [];

  // Header
  blocks.push({
    type: 'header',
    text: { type: 'plain_text', text: '\u{1F4CA} FON Daily BI Report \u2014 ' + report.period.date },
  });

  // Executive summary
  blocks.push({
    type: 'section',
    text: {
      type: 'mrkdwn',
      text: [
        '*MRR:* $' + s.mrr.toLocaleString() + '  |  *ARR:* $' + s.arr.toLocaleString(),
        '*Active subscriptions:* ' + s.activeSubscriptions + '  |  *Churn rate:* ' + s.churnRate + '%',
        '*New today:* ' + s.newSubscriptionsToday + '  |  *Cancelled today:* ' + s.cancellationsToday,
        '*Revenue today:* $' + s.revenueToday.toLocaleString(),
      ].join('\n'),
    },
  });

  // Comparisons
  const cmp = report.comparisons;
  blocks.push({
    type: 'section',
    text: {
      type: 'mrkdwn',
      text: [
        '*Comparisons*',
        cmp.vsYesterday.revenueChange !== null ? 'vs yesterday: ' + (cmp.vsYesterday.revenueChange >= 0 ? '+' : '') + cmp.vsYesterday.revenueChange + '% revenue' : 'vs yesterday: N/A',
        cmp.vsLastWeek.revenueChange !== null ? 'vs last week: ' + (cmp.vsLastWeek.revenueChange >= 0 ? '+' : '') + cmp.vsLastWeek.revenueChange + '% daily avg' : 'vs last week: N/A',
      ].join('\\n'),
    },
  });

  // Trends & Insights (narrative)
  const trendsLines = [];
  const mrrTrend = cmp.vsYesterday.revenueChange;
  if (mrrTrend !== null) {
    if (mrrTrend > 5) trendsLines.push('\u{1F7E2} *Revenue trend:* Up ' + mrrTrend.toFixed(1) + '% vs yesterday — positive momentum.');
    else if (mrrTrend < -5) trendsLines.push('\u{1F534} *Revenue trend:* Down ' + Math.abs(mrrTrend).toFixed(1) + '% vs yesterday — investigate causes.');
    else trendsLines.push('\u{1F7E1} *Revenue trend:* Stable vs yesterday (' + (mrrTrend >= 0 ? '+' : '') + mrrTrend.toFixed(1) + '%).');
  }
  if (s.newSubscriptionsToday > 0) {
    trendsLines.push('\u{1F4C8} *New subscriptions:* ' + s.newSubscriptionsToday + ' new today' + (s.cancellationsToday > 0 ? ' vs ' + s.cancellationsToday + ' cancellations.' : '.'));
  }
  if (s.churnRate > 5) {
    trendsLines.push('\u26A0\uFE0F *Churn risk:* Period churn at ' + s.churnRate + '% — above the 5% threshold.');
  } else {
    trendsLines.push('\u2705 *Churn risk:* Period churn at ' + s.churnRate + '% — within normal range.');
  }
  if (trendsLines.length > 0) {
    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: '*Trends & Insights*\n' + trendsLines.join('\n') },
    });
  }


  // Amplitude DAU section
  const amp = report.metrics.amplitude;
  if (amp && amp.status === 'available' && amp.data) {
    const d = amp.data;
    const dauLines = [];
    dauLines.push('*DAU today:* ' + d.dauToday + '  |  *DAU yesterday:* ' + d.dauYesterday + '  |  *7-day avg:* ' + d.dau7DayAvg);
    dauLines.push('*Total unique users (90d):* ' + d.totalUniqueUsers + '  |  *Events:* ' + d.totalEvents.toLocaleString());
    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: '*Amplitude Active Users*\n' + dauLines.join('\n') },
    });
  }

  // Risks
  const riskLines = [];
  if (report.alerts.length > 0) {
    riskLines.push('\u{1F6A8} ' + report.alerts.length + ' active alert(s) — see Alerts section below.');
  }
  if (s.churnRate > 10) {
    riskLines.push('\u{1F534} Churn rate above 10% — elevated risk of MRR erosion.');
  }
  if (report.metrics.lemonSqueezy.orders.paymentFailureRate > 5) {
    riskLines.push('\u26A0\uFE0F Payment failure rate at ' + report.metrics.lemonSqueezy.orders.paymentFailureRate + '% — may indicate billing issues.');
  }
  if (riskLines.length === 0) {
    riskLines.push('\u2705 No significant risks detected.');
  }
  blocks.push({
    type: 'section',
    text: { type: 'mrkdwn', text: '*Risks*\n' + riskLines.join('\n') },
  });

  // Opportunities
  const oppLines = [];
  if (s.newSubscriptionsToday > s.cancellationsToday * 2) {
    oppLines.push('\u{1F4C8} Strong new-to-cancelled ratio (' + s.newSubscriptionsToday + ':' + s.cancellationsToday + ') — growth opportunity.');
  }
  if (mrrTrend !== null && mrrTrend > 0) {
    oppLines.push('\u{1F4B0} Revenue trending up — consider doubling down on current acquisition channels.');
  }
  if (oppLines.length === 0) {
    oppLines.push('\u{1F50D} No clear opportunities identified. Continue monitoring for emerging trends.');
  }
  blocks.push({
    type: 'section',
    text: { type: 'mrkdwn', text: '*Opportunities*\n' + oppLines.join('\n') },
  });

  // Plain customer timeline
  const plain = report.metrics.plain;
  if (plain && plain.status === 'available' && plain.data && plain.data.data && plain.data.data.customerTimeline) {
    const entries = plain.data.data.customerTimeline.edges || [];
    if (entries.length > 0) {
      const timelineLines = entries.slice(0, 5).map((e) => {
        const node = e.node || {};
        const date = (node.createdAt || '').slice(0, 10);
        const text = node.text || '';
        return '`' + date + '` ' + (text.length > 120 ? text.slice(0, 120) + '...' : text);
      });
      blocks.push({
        type: 'section',
        text: { type: 'mrkdwn', text: '*Customer Timeline (Plain)*\n' + timelineLines.join('\n') },
      });
    }
  }

  // Forecast
  const f = report.forecast;
  blocks.push({
    type: 'section',
    text: {
      type: 'mrkdwn',
      text: '*MRR Forecast*\n30d: $' + f.forecast30d.toLocaleString() + '  |  60d: $' + f.forecast60d.toLocaleString() + '  |  90d: $' + f.forecast90d.toLocaleString(),
    },
  });

  // Alerts
  if (report.alerts.length > 0) {
    const alertText = report.alerts.map((a) => {
      const icon = a.severity === 'critical' ? '\u{1F6A8}' : '\u26A0\uFE0F';
      return icon + ' *' + a.metric + ':* ' + a.message;
    }).join('\n');
    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: '*Alerts (' + report.alerts.length + ')*\n' + alertText },
    });
  }

  // Recommendations
  if (report.recommendations.length > 0) {
    const recText = report.recommendations.map((r) => {
      const icon = r.priority === 'high' ? '\u{1F534}' : r.priority === 'medium' ? '\u{1F7E1}' : '\u{1F7E2}';
      return icon + ' *' + r.action + ':* ' + r.detail;
    }).join('\n');
    blocks.push({
      type: 'section',
      text: { type: 'mrkdwn', text: '*Top Recommendations*\n' + recText },
    });
  }

  return { blocks };
}

// ── Main ─────────────────────────────────────────────────────────────────────────
async function main() {
  const mode = process.argv.includes('--slack') ? 'slack' : process.argv.includes('--alerts-only') ? 'alerts' : 'json';

  // Fetch data
  const [subscriptions, orders, prices, amplitudeData, plainData] = await Promise.all([
    fetchLSSubscriptions(),
    fetchLSOrders(),
    fetchLSPrices(),
    fetchAmplitudeEvents(7),
    fetchPlainTimeline(),
  ]);

  // Build price map: price_id -> { unit_price, renewal_interval_unit, renewal_interval_quantity }
  const priceMap = {};
  if (Array.isArray(prices)) {
    for (const p of prices) {
      const a = p.attributes || {};
      priceMap[p.id] = {
        unit_price: parseInt(a.unit_price || 0),
        renewal_interval_unit: a.renewal_interval_unit || 'year',
        renewal_interval_quantity: parseInt(a.renewal_interval_quantity || 1),
      };
    }
  }

  // Pipeline failure check
  const pipelineAlerts = [];
  if (subscriptions.error) pipelineAlerts.push({ severity: 'critical', metric: 'Data pipeline', message: 'LemonSqueezy subscriptions: ' + subscriptions.error });
  if (orders.error) pipelineAlerts.push({ severity: 'critical', metric: 'Data pipeline', message: 'LemonSqueezy orders: ' + orders.error });

  if (pipelineAlerts.length > 0 && mode === 'alerts') {
    console.log(JSON.stringify({ alerts: pipelineAlerts, generatedAt: new Date().toISOString() }, null, 2));
    process.exit(0);
  }

  // Analysis
  const subAnalysis = Array.isArray(subscriptions) ? analyzeSubscriptions(subscriptions, priceMap) : { error: subscriptions.error };
  const orderAnalysis = Array.isArray(orders) ? analyzeOrders(orders) : { error: orders.error };

  if (subAnalysis.error || orderAnalysis.error) {
    const errs = [subAnalysis.error, orderAnalysis.error].filter(Boolean);
    console.error('Data analysis failed:', errs.join('; '));
    process.exit(1);
  }

  const alerts = [...pipelineAlerts, ...checkAlerts(subAnalysis, orderAnalysis)];
  const forecast = forecastMRR(subAnalysis, orderAnalysis);
  const recs = generateRecommendations(subAnalysis, orderAnalysis, alerts);

  const report = generateReport(subAnalysis, orderAnalysis, alerts, forecast, recs, amplitudeData, plainData);

  if (mode === 'alerts') {
    if (alerts.length > 0) {
      console.log(JSON.stringify({ alerts, generatedAt: report.generatedAt }, null, 2));
    } else {
      process.exit(0); // silent exit — no alerts to report
    }
  } else if (mode === 'slack') {
    const slackPayload = formatSlackReport(report);
    if (SLACK_WEBHOOK_URL) {
      const result = await post(SLACK_WEBHOOK_URL, slackPayload);
      console.log(JSON.stringify({ delivered: true, result }, null, 2));
    } else {
      console.log(JSON.stringify(slackPayload, null, 2));
    }
  } else {
    console.log(JSON.stringify(report, null, 2));
  }
}

main().catch((err) => {
  console.error('Fatal:', err.message);
  process.exit(1);
});
