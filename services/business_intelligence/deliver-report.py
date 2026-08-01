#!/usr/bin/env python3
"""
FON Daily BI Report — delivery script
Runs the toolkit, formats the report, posts to Slack DM + Paperclip comment.
"""
import json
import os
import subprocess
import urllib.request

# ── Fetch secrets ──
def get_secret(secret_id):
    return os.popen(
        f"aws secretsmanager get-secret-value --secret-id {secret_id} "
        f"--region us-east-1 --query SecretString --output text"
    ).read().strip()

ls_key = get_secret("agentos/piper/lemonsqueezy_api_key")
plain_key_raw = get_secret("agentos/piper/plain_api_key")
plain_key = json.loads(plain_key_raw).get("api_key", "") if plain_key_raw else ""
# NOTE: chrisabad/amplitude/api_key is the chrisabad.com PORTFOLIO project's key,
# not Font Replacer's. Do not point this back at it. FON's own Amplitude project
# keys are project-scoped under agentos/fon/*.
amplitude_key = get_secret("agentos/fon/amplitude_api_key")
amplitude_secret = get_secret("agentos/fon/amplitude_secret_key")
slack_token = get_secret("agentos/juno/slack_bot_token")

# ── Run toolkit ──
env = os.environ.copy()
env["LEMONSQUEEZY_API_KEY"] = ls_key
env["PLAIN_API_KEY"] = plain_key
env["AMPLITUDE_API_KEY"] = amplitude_key
env["AMPLITUDE_SECRET_KEY"] = amplitude_secret

result = subprocess.run(
    ["node", "/paperclip/repos/agentos-services/services/business_intelligence/bi-toolkit.cjs"],
    capture_output=True, text=True, timeout=120, env=env
)
report = json.loads(result.stdout)

# ── Format report ──
es = report["executiveSummary"]
ls_data = report["metrics"]["lemonSqueezy"]
subs = ls_data["subscriptions"]
orders = ls_data["orders"]
daily = subs["daily"]
fc = report["forecast"]
cmp = report["comparisons"]
alerts = report["alerts"]
recs = report["recommendations"]

lines = []
lines.append("=" * 60)
lines.append(f"  FON DAILY BI REPORT — {report['period']['date']}")
lines.append("=" * 60)
lines.append("")
lines.append("EXECUTIVE SUMMARY")
lines.append("-" * 40)
lines.append(f"  MRR:              ${es['mrr']:>8,.2f}/mo")
lines.append(f"  ARR:              ${es['arr']:>8,.2f}/yr")
lines.append(f"  Active subs:      {es['activeSubscriptions']:>8}")
lines.append(f"  Churn rate:       {es['churnRate']:>7.2f}%")
lines.append(f"  New today:        {es['newSubscriptionsToday']:>8}")
lines.append(f"  Cancelled today:  {es['cancellationsToday']:>8}")
lines.append(f"  Revenue today:    ${es['revenueToday']:>8,.2f}")
lines.append("")
lines.append("TRENDS & COMPARISONS")
lines.append("-" * 40)
if cmp["vsYesterday"]["revenueChange"] is not None:
    sign = "+" if cmp["vsYesterday"]["revenueChange"] >= 0 else ""
    lines.append(f"  vs yesterday:     {sign}{cmp['vsYesterday']['revenueChange']}% revenue")
else:
    lines.append("  vs yesterday:     N/A")
if cmp["vsLastWeek"]["revenueChange"] is not None:
    sign = "+" if cmp["vsLastWeek"]["revenueChange"] >= 0 else ""
    lines.append(f"  vs last week avg: {sign}{cmp['vsLastWeek']['revenueChange']}% revenue")
else:
    lines.append("  vs last week avg: N/A")
lines.append("")
lines.append("PLAN BREAKDOWN")
lines.append("-" * 40)
for plan in subs["byPlan"]:
    lines.append(f"  {plan['name']:<30} {plan['active']:>4} active  ${plan['mrr']:>7,.2f} MRR  {plan['cancelled']:>4} cancelled")
lines.append("")
lines.append("DAILY ACTIVITY")
lines.append("-" * 40)
lines.append(f"  Today:            {daily['today']['new']} new, {daily['today']['cancelled']} cancelled")
lines.append(f"  Yesterday:        {daily['yesterday']['new']} new, {daily['yesterday']['cancelled']} cancelled")
lines.append(f"  Last 7 days:      {daily['lastWeek']['new']} new, {daily['lastWeek']['cancelled']} cancelled")
lines.append(f"  Last 30 days:     {daily['lastMonth']['new']} new, {daily['lastMonth']['cancelled']} cancelled")
lines.append("")
lines.append("REVENUE")
lines.append("-" * 40)
lines.append(f"  Yesterday:        ${orders['yesterday']['revenue']:>8,.2f}  ({orders['yesterday']['orders']} orders)")
lines.append(f"  Last 7 days:      ${orders['lastWeekRevenue']:>8,.2f}")
lines.append(f"  Payment failures: {orders['today']['paymentFailures']} today ({orders['paymentFailureRate']}%)")
lines.append("")
lines.append("MRR FORECAST")
lines.append("-" * 40)
lines.append(f"  30-day:           ${fc['forecast30d']:>8,.2f}")
lines.append(f"  60-day:           ${fc['forecast60d']:>8,.2f}")
lines.append(f"  90-day:           ${fc['forecast90d']:>8,.2f}")
lines.append(f"  Daily net avg:    ${fc['dailyNetAverage']:>8,.2f}")
lines.append(f"  Monthly trend:    ${fc['monthlyNetTrend']:>8,.2f}")
lines.append("")
if alerts:
    lines.append("ALERTS")
    lines.append("-" * 40)
    for a in alerts:
        icon = "🔴" if a["severity"] == "critical" else "⚠️"
        lines.append(f"  {icon} [{a['severity'].upper()}] {a['metric']}: {a['message']}")
    lines.append("")
if recs:
    lines.append("TOP RECOMMENDATIONS")
    lines.append("-" * 40)
    for r in recs:
        icon = "🔴" if r["priority"] == "high" else "🟡" if r["priority"] == "medium" else "🟢"
        lines.append(f"  {icon} [{r['priority'].upper()}] {r['action']}")
        lines.append(f"     {r['detail']}")
    lines.append("")

lines.append("AMPLITUDE ACTIVE USERS")
lines.append("-" * 40)
amp = report.get("metrics", {}).get("amplitude", {})
if amp.get("status") == "available" and amp.get("data"):
    d = amp["data"]
    lines.append(f"  DAU today:        {d.get('dauToday', 'N/A'):>8}")
    lines.append(f"  DAU yesterday:    {d.get('dauYesterday', 'N/A'):>8}")
    lines.append(f"  7-day avg DAU:    {d.get('dau7DayAvg', 'N/A'):>8}")
    lines.append(f"  Total unique (90d): {d.get('totalUniqueUsers', 'N/A'):>8}")
    lines.append(f"  Events in window: {d.get('totalEvents', 'N/A'):>8}")
else:
    lines.append(f"  Status: {amp.get('status', 'unavailable')}")
    if amp.get("error"):
        lines.append(f"  Error: {amp['error']}")
lines.append("")
lines.append("DATA SOURCES")
lines.append("-" * 40)
lines.append(f"  LemonSqueezy:     OK ({subs['totalSubscriptions']} subscriptions)")
lines.append(f"  Amplitude:        {report['metrics']['amplitude']['status']}")
if "error" in report["metrics"]["amplitude"]:
    lines.append(f"    Error: {report['metrics']['amplitude']['error']}")
lines.append(f"  Plain:            {report['metrics']['plain']['status']}")
if "error" in report["metrics"]["plain"] or (report["metrics"]["plain"].get("data",{}).get("error")):
    lines.append(f"    Error: {report['metrics']['plain'].get('error') or report['metrics']['plain']['data'].get('error')}")
lines.append("")
lines.append("=" * 60)
lines.append(f"  Generated: {report['generatedAt']}")
lines.append("=" * 60)

report_text = "\n".join(lines)

# ── Deliver to Slack DM (Keona) ──
slack_payload = {
    "channel": "D0BJ70DL739",
    "text": f"📊 FON Daily BI Report — {report['period']['date']}",
    "blocks": [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"📊 FON Daily BI Report — {report['period']['date']}"}
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*MRR:* ${es['mrr']:,.2f}/mo  |  *ARR:* ${es['arr']:,.2f}/yr\n"
                    f"*Active subs:* {es['activeSubscriptions']}  |  *Churn rate:* {es['churnRate']}%\n"
                    f"*New today:* {es['newSubscriptionsToday']}  |  *Cancelled today:* {es['cancellationsToday']}\n"
                    f"*Revenue today:* ${es['revenueToday']:,.2f}"
                )
            }
        }
    ]
}

# ── Trends & Insights ──
trends_lines = []
mrr_trend = cmp.get("vsYesterday", {}).get("revenueChange")
if mrr_trend is not None:
    if mrr_trend > 5:
        trends_lines.append(f"🟢 *Revenue trend:* Up {mrr_trend:.1f}% vs yesterday — positive momentum.")
    elif mrr_trend < -5:
        trends_lines.append(f"🔴 *Revenue trend:* Down {abs(mrr_trend):.1f}% vs yesterday — investigate causes.")
    else:
        trends_lines.append(f"🟡 *Revenue trend:* Stable vs yesterday ({'+' if mrr_trend >= 0 else ''}{mrr_trend:.1f}%).")
if es["newSubscriptionsToday"] > 0:
    trends_lines.append(f"📈 *New subscriptions:* {es['newSubscriptionsToday']} new today" + (f" vs {es['cancellationsToday']} cancellations." if es['cancellationsToday'] > 0 else "."))
if es["churnRate"] > 5:
    trends_lines.append(f"⚠️ *Churn risk:* Period churn at {es['churnRate']}% — above the 5% threshold.")
else:
    trends_lines.append(f"✅ *Churn risk:* Period churn at {es['churnRate']}% — within normal range.")
if trends_lines:
    slack_payload["blocks"].append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*Trends & Insights*\n" + "\n".join(trends_lines)}
    })

# ── Risks ──
risk_lines = []
if alerts:
    risk_lines.append(f"🚨 {len(alerts)} active alert(s) — see Alerts section below.")
if es["churnRate"] > 10:
    risk_lines.append("🔴 Churn rate above 10% — elevated risk of MRR erosion.")
if ls_data["orders"]["paymentFailureRate"] > 5:
    risk_lines.append(f"⚠️ Payment failure rate at {ls_data['orders']['paymentFailureRate']}% — may indicate billing issues.")
if not risk_lines:
    risk_lines.append("✅ No significant risks detected.")
slack_payload["blocks"].append({
    "type": "section",
    "text": {"type": "mrkdwn", "text": "*Risks*\n" + "\n".join(risk_lines)}
})

# ── Opportunities ──
opp_lines = []
if es["newSubscriptionsToday"] > es["cancellationsToday"] * 2:
    opp_lines.append(f"📈 Strong new-to-cancelled ratio ({es['newSubscriptionsToday']}:{es['cancellationsToday']}) — growth opportunity.")
if mrr_trend is not None and mrr_trend > 0:
    opp_lines.append("💰 Revenue trending up — consider doubling down on current acquisition channels.")
if not opp_lines:
    opp_lines.append("🔍 No clear opportunities identified. Continue monitoring for emerging trends.")
slack_payload["blocks"].append({
    "type": "section",
    "text": {"type": "mrkdwn", "text": "*Opportunities*\n" + "\n".join(opp_lines)}
})


# ── Amplitude DAU ──
amp = report.get("metrics", {}).get("amplitude", {})
if amp.get("status") == "available" and amp.get("data"):
    d = amp["data"]
    dau_lines = []
    dau_lines.append(f"*DAU today:* {d.get('dauToday', 'N/A')}  |  *DAU yesterday:* {d.get('dauYesterday', 'N/A')}  |  *7-day avg:* {d.get('dau7DayAvg', 'N/A')}")
    dau_lines.append(f"*Total unique users (90d):* {d.get('totalUniqueUsers', 'N/A')}  |  *Events:* {d.get('totalEvents', 0):,}")
    slack_payload["blocks"].append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*Amplitude Active Users*\n" + "\n".join(dau_lines)}
    })

# ── Plain Customer Timeline ──
plain = report["metrics"]["plain"]
plain_data = plain.get("data", {}) if plain.get("status") == "available" else {}
if plain_data and plain_data.get("recentActivity"):
    timeline_lines = []
    for act in plain_data["recentActivity"][:5]:
        name = act.get("customer", "Unknown")
        count = act.get("recentEntryCount", 0)
        latest = act.get("latestEntry", {})
        ts = (latest.get("timestamp", "") or "")[:10]
        entry_type = (latest.get("type", "") or "").replace("Entry", "")
        subject = latest.get("subject", "") or ""
        detail = subject or entry_type
        timeline_lines.append(f"• *{name}* — {count} entries, latest: {ts} ({detail})")
    if timeline_lines:
        slack_payload["blocks"].append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*Customer Activity (Plain)*\n" + "\n".join(timeline_lines)}
        })
    # Add summary stats
    summary_line = f"_{plain_data['totalCustomers']} total customers, {plain_data['activeCustomers']} active, {plain_data['customersWithActivity']} with recent activity_"
    slack_payload["blocks"].append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": summary_line}]
    })

# Add alerts
if alerts:
    alert_text = "\n".join([
        f"{'🔴' if a['severity']=='critical' else '⚠️'} *{a['metric']}:* {a['message']}"
        for a in alerts
    ])
    slack_payload["blocks"].append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Alerts ({len(alerts)})*\n{alert_text}"}
    })

# Add recommendations
if recs:
    rec_text = "\n".join([
        f"{'🔴' if r['priority']=='high' else '🟡' if r['priority']=='medium' else '🟢'} *{r['action']}:* {r['detail']}"
        for r in recs
    ])
    slack_payload["blocks"].append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Top Recommendations*\n{rec_text}"}
    })

# Add forecast
slack_payload["blocks"].append({
    "type": "section",
    "text": {
        "type": "mrkdwn",
        "text": (
            f"*MRR Forecast*\n"
            f"30d: ${fc['forecast30d']:,.2f}  |  60d: ${fc['forecast60d']:,.2f}  |  90d: ${fc['forecast90d']:,.2f}"
        )
    }
})

# Add data sources status
src_lines = []
src_lines.append(f"✅ LemonSqueezy ({subs['totalSubscriptions']} subs)")
if report["metrics"]["amplitude"]["status"] == "available":
    src_lines.append("✅ Amplitude")
else:
    src_lines.append(f"❌ Amplitude — {report['metrics']['amplitude'].get('error','unavailable')}")
if report["metrics"]["plain"]["status"] == "available":
    src_lines.append("✅ Plain")
else:
    src_lines.append(f"❌ Plain — {report['metrics']['plain'].get('error','unavailable')}")

slack_payload["blocks"].append({
    "type": "section",
    "text": {"type": "mrkdwn", "text": "*Data Sources*\n" + "\n".join(src_lines)}
})

# Post to Slack
req = urllib.request.Request(
    "https://slack.com/api/chat.postMessage",
    data=json.dumps(slack_payload).encode(),
    headers={
        "Authorization": f"Bearer {slack_token}",
        "Content-Type": "application/json"
    },
    method="POST"
)
resp = urllib.request.urlopen(req)
slack_result = json.loads(resp.read())
print(f"Slack delivery: {'OK' if slack_result.get('ok') else 'FAILED'}")
if not slack_result.get("ok"):
    print(f"  Error: {slack_result.get('error')}")

# ── Also print to stdout for Paperclip comment ──
print()
print(report_text)
