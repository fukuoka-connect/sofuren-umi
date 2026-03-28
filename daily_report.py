#!/usr/bin/env python3
"""
Fukuoka connect 日次アクセス解析レポート
毎日朝9時（JST）に自動実行
FUKUOKA-CONNECT公式LINEからクライアント個別に配信
"""

import os
import json
import datetime
import requests
import anthropic
from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import (
    RunReportRequest, Dimension, Metric, DateRange
)
from googleapiclient.discovery import build

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# クライアント設定
# 新規追加時はここにブロックを1つ追加するだけ！
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLIENTS = [
    {
        "name":         "想夫恋 宇美店",
        "ga4_id":       "529552579",
        "site_url":     "https://fukuoka-connect.github.io/sofuren-umi/",
        "line_user_id": os.environ.get("LINE_USER_ID", ""),
    },
    {
        "name":         "想夫恋 ふりかけLP",
        "ga4_id":       "529527426",
        "site_url":     "https://sofurenumi-glitch.github.io/sofuren-furikake/",
        "line_user_id": os.environ.get("LINE_USER_ID", ""),
    },
    # ── 新規クライアント追加例 ──────────────────
    # {
    #     "name":         "セラ",
    #     "ga4_id":       "XXXXXXXXX",
    #     "site_url":     "https://fukuoka-connect.github.io/sera/",
    #     "line_user_id": "Uxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    # },
]

# ── 環境変数 ──────────────────────────────────
LINE_TOKEN        = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
GCP_KEY_JSON      = os.environ["GCP_SERVICE_ACCOUNT_KEY"]

# ── Google認証 ────────────────────────────────
def get_credentials():
    key_dict = json.loads(GCP_KEY_JSON)
    scopes = [
        "https://www.googleapis.com/auth/analytics.readonly",
        "https://www.googleapis.com/auth/webmasters.readonly",
    ]
    return service_account.Credentials.from_service_account_info(
        key_dict, scopes=scopes
    )

# ── GA4データ取得 ─────────────────────────────
def get_ga4_data(creds, ga4_id):
    client = BetaAnalyticsDataClient(credentials=creds)
    today     = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    week_ago  = yesterday - datetime.timedelta(days=7)
    month_ago = yesterday - datetime.timedelta(days=30)

    def report(start, end, metrics, dims=None):
        return client.run_report(RunReportRequest(
            property=f"properties/{ga4_id}",
            dimensions=[Dimension(name=d) for d in (dims or [])],
            metrics=[Metric(name=m) for m in metrics],
            date_ranges=[DateRange(
                start_date=str(start), end_date=str(end)
            )],
        ))

    def v(r, row=0, col=0):
        try: return r.rows[row].metric_values[col].value
        except: return "0"

    r_day = report(yesterday, yesterday,
        ["sessions","totalUsers","bounceRate","screenPageViews"])
    r_lw  = report(week_ago,  week_ago,  ["sessions"])
    r_lm  = report(month_ago, month_ago, ["sessions"])
    r_ev  = report(yesterday, yesterday, ["eventCount"], ["eventName"])
    r_ch  = report(yesterday, yesterday, ["sessions"],
        ["sessionDefaultChannelGroup"])

    events = {}
    for row in r_ev.rows:
        events[row.dimension_values[0].value] = int(
            row.metric_values[0].value)

    channels = {}
    for row in r_ch.rows:
        channels[row.dimension_values[0].value] = int(
            row.metric_values[0].value)

    return {
        "date":        str(yesterday),
        "sessions":    int(v(r_day, 0, 0)),
        "users":       int(v(r_day, 0, 1)),
        "bounce_rate": round(float(v(r_day, 0, 2)) * 100, 1),
        "pageviews":   int(v(r_day, 0, 3)),
        "sessions_lw": int(v(r_lw,  0, 0)),
        "sessions_lm": int(v(r_lm,  0, 0)),
        "events":      events,
        "channels":    channels,
    }

# ── Search Console データ取得 ─────────────────
def get_sc_data(creds, site_url):
    svc = build("searchconsole", "v1", credentials=creds)
    today = datetime.date.today()
    end   = today - datetime.timedelta(days=3)
    start = end   - datetime.timedelta(days=7)
    try:
        res = svc.searchanalytics().query(
            siteUrl=site_url,
            body={
                "startDate": str(start),
                "endDate":   str(end),
                "dimensions": ["query"],
                "rowLimit": 5,
                "orderBy": [{"fieldName": "clicks",
                             "sortOrder": "DESCENDING"}],
            }
        ).execute()
        rows = res.get("rows", [])
        keywords, total_clicks, total_imp = [], 0, 0
        for row in rows:
            c = int(row.get("clicks", 0))
            i = int(row.get("impressions", 0))
            keywords.append({
                "query":       row["keys"][0],
                "clicks":      c,
                "impressions": i,
                "position":    round(row.get("position", 0), 1),
            })
            total_clicks += c
            total_imp    += i
        return {"keywords": keywords,
                "total_clicks": total_clicks,
                "total_impressions": total_imp}
    except Exception as e:
        print(f"SC error ({site_url}): {e}")
        return {"keywords": [], "total_clicks": 0, "total_impressions": 0}

# ── Claude AIアドバイス生成 ───────────────────
def generate_advice(name, ga4, sc):
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    lw_diff = ga4["sessions"] - ga4["sessions_lw"]
    lm_diff = ga4["sessions"] - ga4["sessions_lm"]
    kw_str  = ", ".join([k["query"] for k in sc["keywords"][:3]]) or "なし"

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=200,
        messages=[{"role": "user", "content": f"""
あなたは飲食店のデジタルマーケティング専門家です。
「{name}」のデータをもとに、オーナーへの具体的なアドバイスを
2文以内で生成してください。日本語で親しみやすく、
今日すぐ実行できる内容にしてください。

セッション: {ga4['sessions']}件（先週比{lw_diff:+d}・先月比{lm_diff:+d}）
直帰率: {ga4['bounce_rate']}%
電話タップ: {ga4['events'].get('phone_call', 0)}件
LINEクリック: {ga4['events'].get('line_click', 0)}件
検索クリック: {sc['total_clicks']}件
TOP検索ワード: {kw_str}
"""}]
    )
    return msg.content[0].text

# ── レポート文章生成 ──────────────────────────
def build_report(name, ga4, sc, advice):
    lw_diff = ga4["sessions"] - ga4["sessions_lw"]
    lm_diff = ga4["sessions"] - ga4["sessions_lm"]
    lw_icon = "↑" if lw_diff >= 0 else "↓"
    lm_icon = "↑" if lm_diff >= 0 else "↓"

    ch_map = {
        "Organic Search": "🔍 検索",
        "Direct":         "📌 直接",
        "Organic Social": "📱 SNS",
        "Referral":       "🔗 参照",
    }
    ch_lines = ""
    for ch, cnt in sorted(
        ga4["channels"].items(), key=lambda x: x[1], reverse=True
    )[:4]:
        ch_lines += f"  {ch_map.get(ch, ch)}: {cnt}件\n"
    if not ch_lines:
        ch_lines = "  （データなし）\n"

    kw_lines = ""
    for i, kw in enumerate(sc["keywords"][:3], 1):
        kw_lines += (
            f"  {i}. {kw['query']}\n"
            f"     表示{kw['impressions']}回・"
            f"クリック{kw['clicks']}回・{kw['position']}位\n"
        )
    if not kw_lines:
        kw_lines = "  （データ集計中）\n"

    return f"""📊 {name}
アクセスレポート {ga4['date']}

━━━━━━━━━━━━━
📈 昨日のアクセス
━━━━━━━━━━━━━
セッション: {ga4['sessions']}件
  先週比: {lw_icon}{abs(lw_diff)}件
  先月比: {lm_icon}{abs(lm_diff)}件
ユーザー: {ga4['users']}人
PV: {ga4['pageviews']}
直帰率: {ga4['bounce_rate']}%

━━━━━━━━━━━━━
📞 CVアクション
━━━━━━━━━━━━━
電話タップ: {ga4['events'].get('phone_call', 0)}件
LINEクリック: {ga4['events'].get('line_click', 0)}件
マップ開く: {ga4['events'].get('map_click', 0)}件
メニュー表示: {ga4['events'].get('menu_modal_open', 0)}件

━━━━━━━━━━━━━
🔀 流入チャネル
━━━━━━━━━━━━━
{ch_lines}
━━━━━━━━━━━━━
🔍 検索キーワード
━━━━━━━━━━━━━
{kw_lines}
━━━━━━━━━━━━━
💡 今日のアドバイス
━━━━━━━━━━━━━
{advice}

Powered by Fukuoka connect"""

# ── LINE送信（クライアント個別）────────────────
def send_line(user_id, message):
    res = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_TOKEN}",
        },
        json={
            "to": user_id,
            "messages": [{"type": "text", "text": message}]
        },
        timeout=10,
    )
    print(f"LINE送信 → {user_id[:8]}...: {res.status_code}")
    if res.status_code != 200:
        print(res.text)

# ── メイン ───────────────────────────────────
def main():
    print("=== Fukuoka connect 日次レポート開始 ===")
    creds = get_credentials()

    for client in CLIENTS:
        print(f"\n--- {client['name']} ---")

        if not client.get("line_user_id"):
            print("LINE_USER_IDが未設定のためスキップ")
            continue

        ga4    = get_ga4_data(creds, client["ga4_id"])
        sc     = get_sc_data(creds, client["site_url"])
        advice = generate_advice(client["name"], ga4, sc)
        report = build_report(client["name"], ga4, sc, advice)

        print(report)
        send_line(client["line_user_id"], report)

    print("\n=== 全クライアント完了 ===")

if __name__ == "__main__":
    main()
