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

// ── Config ──────────────────────────────────────────────────────────────────────
const LS_API_BASE = 'https://api.lemonsqueezy.com/v1';
const AMPLITUDE_EXPORT_BASE = 'https://amplitude.com/api/2';
const AMPLITUDE_DASHBOARD_BASE = 'https://amplitude.com/api';
const PLAIN_API_BASE = 'https://api.plain.com/graphql';

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
// The Amplitude /2/export endpoint is NOT a lightweight "active users" query — it
// returns a multi-MB zip of gzipped NDJSON event files meant for warehouse ETL, and
// requires unzip + gunzip + per-line JSON parsing to turn into a DAU number. That's
// real engineering (see FON follow-up issue), not something to rush into a script
// that a daily cron depends on. Pulling that payload here also risks blowing
// deliver-report.py's 30s subprocess timeout and crashing the whole report.
// Until the export-parsing pipeline is built, verify credentials cheaply via the
// Dashboard REST API's taxonomy endpoint (fast, plain JSON, no auth surprises) and
// report Amplitude as explicitly "not yet implemented" rather than silently
// returning unparsed zip bytes as if they were usable data.
async function fetchAmplitudeEvents(daysBack = 90) {
  if (!AMPLITUDE_API_KEY || !AMPLITUDE_SECRET_KEY) return { error: 'AMPLITUDE_API_KEY or AMPLITUDE_SECRET_KEY not set' };

  const auth = Buffer.from(AMPLITUDE_API_KEY + ':' + AMPLITUDE_SECRET_KEY).toString('base64');
  const probe = await fetch(AMPLITUDE_DASHBOARD_BASE + '/2/taxonomy/event', { headers: { Authorization: 'Basic ' + auth } });

  if (probe && probe.error) {
    return { error: 'Amplitude credentials invalid: ' + JSON.stringify(probe.error) };
  }
  if (!probe || probe.parseError) {
    return { error: 'Amplitude credential check returned an unexpected (non-JSON) response' };
  }

  // Credentials verified live against Amplitude. Real usage-metrics extraction
  // (DAU, active users, event correlation) needs the export zip/gunzip/NDJSON
  // pipeline — not yet implemented. See FON follow-up issue.
  return { error: 'Amplitude credentials verified OK — usage-metrics pipeline (zip export parsing) not yet implemented' };
}

// ── Plain ────────────────────────────────────────────────────────────────────────
async function fetchPlainTimeline() {
  if (!PLAIN_API_KEY) return { error: 'PLAIN_API_KEY not set' };

  const query = {
    query: '{\n      customerTimeline(first: 50, orderBy: { field: createdAt, direction: DESC }) {\n        edges {\n          node {\n            id\n            createdAt\n            ... on CustomerTimelineEntryText {\n              text\n            }\n          }\n        }\n      }\n    }',
  };

  try {
    return await post(PLAIN_API_BASE, query, { Authorization: 'Bearer ' + PLAIN_API_KEY });
  } catch (err) {
    return { error: 'Plain API unreachable: ' + err.message };
  }
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

  if (subAnalysis.churnRate > 5) {
    recs.push({ priority: 'high', action: 'Investigate churn drivers', detail: 'Current churn rate is ' + subAnalysis.churnRate + '%. Review cancellation reasons and identify at-risk cohorts.' });
  }
  if (orderAnalysis.today.paymentFailures > 0) {
    recs.push({ priority: 'high', action: 'Review payment failures', detail: orderAnalysis.today.paymentFailures + ' payment failures today. Check billing system and notify affected customers.' });
  }
  if (subAnalysis.daily.today.new < subAnalysis.daily.lastWeek.new / 7 * 0.7) {
    recs.push({ priority: 'medium', action: 'New subscriptions below trend', detail: 'New subscriptions today (' + subAnalysis.daily.today.new + ') are significantly below the weekly daily average.' });
  }
  if (subAnalysis.mrr > 0 && subAnalysis.daily.lastMonth.new > subAnalysis.daily.lastMonth.cancelled) {
    recs.push({ priority: 'medium', action: 'Net subscriber growth', detail: 'Net positive growth this month: ' + (subAnalysis.daily.lastMonth.new - subAnalysis.daily.lastMonth.cancelled) + ' more new than cancelled subscriptions.' });
  }
  if (orderAnalysis.yesterday.revenue > 0 && orderAnalysis.yesterday.revenue > orderAnalysis.dayBefore.revenue * 1.1) {
    recs.push({ priority: 'low', action: 'Revenue up day-over-day', detail: 'Yesterday\'s revenue ($' + orderAnalysis.yesterday.revenue.toFixed(2) + ') was up vs day-before ($' + orderAnalysis.dayBefore.revenue.toFixed(2) + '). Identify what drove the increase.' });
  }
  if (alerts.length === 0) {
    recs.push({ priority: 'low', action: 'No action required', detail: 'All metrics within normal thresholds. Continue monitoring.' });
  }

  // Always return exactly 3
  while (recs.length < 3) {
    recs.push({ priority: 'low', action: 'Monitor trends', detail: 'No specific action needed. Continue monitoring key metrics.' });
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
    fetchAmplitudeEvents(90),
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
