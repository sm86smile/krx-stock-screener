import os
import io
import time
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import numpy as np
import pandas as pd
import requests


# ============================================================
# 1. 기본 설정
# ============================================================

KST = timezone(timedelta(hours=9))

AUTH_KEY = os.getenv("KRX_AUTH_KEY", "")
MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
MAIL_APP_PASSWORD = os.getenv("MAIL_APP_PASSWORD", "")
MAIL_TO = os.getenv("MAIL_TO", "")

if not AUTH_KEY:
    raise ValueError("KRX_AUTH_KEY가 설정되어 있지 않습니다.")

if not MAIL_USERNAME or not MAIL_APP_PASSWORD or not MAIL_TO:
    raise ValueError("메일 발송용 Secret이 부족합니다. MAIL_USERNAME, MAIL_APP_PASSWORD, MAIL_TO를 확인하세요.")


API_BASE = "https://data-dbg.krx.co.kr/svc/apis"

URLS = {
    "KOSPI_INDEX": f"{API_BASE}/idx/kospi_dd_trd",
    "KOSDAQ_INDEX": f"{API_BASE}/idx/kosdaq_dd_trd",
    "KOSPI_STOCK": f"{API_BASE}/sto/stk_bydd_trd",
    "KOSDAQ_STOCK": f"{API_BASE}/sto/ksq_bydd_trd",
}


# ============================================================
# 2. 자동 리포트 조회기간
# ============================================================
# 매일 오전 7시에는 당일 장이 열리기 전이므로,
# END_DATE는 오늘 날짜까지로 두되, KRX에서 실제 데이터가 있는 마지막 거래일까지만 사용됩니다.
# 기본 조회기간은 최근 1년입니다.

today_kst = datetime.now(KST).date()
start_date = today_kst - timedelta(days=180)

KOSPI_START_DATE = start_date.strftime("%Y%m%d")
KOSPI_END_DATE = today_kst.strftime("%Y%m%d")

KOSDAQ_START_DATE = start_date.strftime("%Y%m%d")
KOSDAQ_END_DATE = today_kst.strftime("%Y%m%d")


# ============================================================
# 3. 스크리닝 기준값
# ============================================================

# 지수 하락일 기준: -0.5%
DOWN_THRESHOLD = -0.005

# 1차 스크리닝
MIN_N_DAYS_RATIO = 1.00
MIN_N_DOWN_DAYS_RATIO = 1.00
MIN_AVG_TRDVAL = 5_000_000_000
MIN_AVG_TRDVAL_EOK = 50
MIN_AVG_EXCESS_DOWN_1ST = 0.0
MIN_DEFENSE_RATE_1ST = 0.55
MAX_DOWNSIDE_CAPTURE_1ST = 1.0

# 최종 스크리닝
MIN_DEFENSE_RATE_FINAL = 0.60
MIN_POSITIVE_ON_DOWN_RATE_FINAL = 0.25
MIN_AVG_EXCESS_DOWN_FINAL = 0.005
MAX_DOWNSIDE_CAPTURE_FINAL = 0.80
MAX_DOWN_BETA_FINAL = 0.80
MIN_PERIOD_EXCESS_RETURN_FINAL = 0.0

TOP_N = 30
SLEEP_SEC = 0.03


# ============================================================
# 4. 공통 함수
# ============================================================

def extract_rows(json_data):
    if not isinstance(json_data, dict):
        return []

    for key in ["OutBlock_1", "output", "data"]:
        if key in json_data and isinstance(json_data[key], list):
            return json_data[key]

    for value in json_data.values():
        if isinstance(value, list):
            return value

    return []


def krx_get(url, bas_dd, auth_key=AUTH_KEY):
    headers = {"AUTH_KEY": auth_key}
    params = {"basDd": bas_dd}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=20)
        res.raise_for_status()
        rows = extract_rows(res.json())
        time.sleep(SLEEP_SEC)
        return pd.DataFrame(rows)
    except Exception:
        time.sleep(SLEEP_SEC)
        return pd.DataFrame()


def make_bdate_list(start_date, end_date):
    dates = pd.bdate_range(
        pd.to_datetime(start_date),
        pd.to_datetime(end_date)
    )
    return [d.strftime("%Y%m%d") for d in dates]


def fetch_range(url, start_date, end_date, desc="fetch"):
    date_list = make_bdate_list(start_date, end_date)
    result = []

    for d in date_list:
        df = krx_get(url, d)
        if not df.empty:
            df["API_CALL_DATE"] = d
            result.append(df)

    if not result:
        return pd.DataFrame()

    return pd.concat(result, ignore_index=True)


def to_number(series):
    return (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .replace("-", np.nan)
        .replace("", np.nan)
        .replace("nan", np.nan)
        .astype(float)
    )


def find_col(df, candidates):
    for col in candidates:
        if col in df.columns:
            return col
    return None


# ============================================================
# 5. 데이터 정리 함수
# ============================================================

def standardize_stock_df(raw_df, market):
    if raw_df.empty:
        return pd.DataFrame()

    df = raw_df.copy()

    col_date = find_col(df, ["BAS_DD", "basDd", "기준일자", "API_CALL_DATE"])
    col_code = find_col(df, ["ISU_CD", "isuCd", "종목코드", "ISU_SRT_CD"])
    col_name = find_col(df, ["ISU_NM", "isuNm", "종목명", "ISU_ABBRV"])
    col_close = find_col(df, ["TDD_CLSPRC", "tddClsprc", "종가"])
    col_volume = find_col(df, ["ACC_TRDVOL", "accTrdvol", "거래량"])
    col_value = find_col(df, ["ACC_TRDVAL", "accTrdval", "거래대금"])

    required = {
        "date": col_date,
        "code": col_code,
        "name": col_name,
        "close": col_close,
        "volume": col_volume,
        "trade_value": col_value,
    }

    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(f"종목 데이터 필수 컬럼 누락: {missing}, 현재 컬럼: {df.columns.tolist()}")

    out = pd.DataFrame({
        "date": df[col_date],
        "code": df[col_code],
        "name": df[col_name],
        "close": to_number(df[col_close]),
        "volume": to_number(df[col_volume]),
        "trade_value": to_number(df[col_value]),
        "market": market,
    })

    out["date"] = pd.to_datetime(out["date"], format="%Y%m%d", errors="coerce")
    out = out.dropna(subset=["date", "code", "close"])
    out = out.sort_values(["code", "date"])

    return out


def standardize_index_df(raw_df, main_index_keyword):
    if raw_df.empty:
        return pd.DataFrame()

    df = raw_df.copy()

    col_date = find_col(df, ["BAS_DD", "basDd", "기준일자", "API_CALL_DATE"])
    col_name = find_col(df, ["IDX_NM", "idxNm", "지수명"])
    col_close = find_col(df, ["CLSPRC_IDX", "clsprcIdx", "종가", "TDD_CLSPRC"])

    required = {
        "date": col_date,
        "index_name": col_name,
        "index_close": col_close,
    }

    missing = [k for k, v in required.items() if v is None]
    if missing:
        raise ValueError(f"지수 데이터 필수 컬럼 누락: {missing}, 현재 컬럼: {df.columns.tolist()}")

    out = pd.DataFrame({
        "date": df[col_date],
        "index_name": df[col_name],
        "index_close": to_number(df[col_close]),
    })

    out["date"] = pd.to_datetime(out["date"], format="%Y%m%d", errors="coerce")
    out = out.dropna(subset=["date", "index_name", "index_close"])

    if main_index_keyword.upper() == "KOSPI":
        priority_names = ["코스피", "KOSPI"]
        contains_pattern = "코스피|KOSPI"
    else:
        priority_names = ["코스닥", "KOSDAQ"]
        contains_pattern = "코스닥|KOSDAQ"

    exact = out[out["index_name"].astype(str).isin(priority_names)].copy()

    if not exact.empty:
        out = exact
    else:
        temp = out[
            out["index_name"].astype(str).str.contains(
                contains_pattern,
                case=False,
                regex=True
            )
        ].copy()

        exclude_pattern = "200|100|50|150|대형|중형|소형|배당|성장|가치|섹터"

        temp2 = temp[
            ~temp["index_name"].astype(str).str.contains(
                exclude_pattern,
                case=False,
                regex=True
            )
        ].copy()

        out = temp2 if not temp2.empty else temp

    out = out[["date", "index_name", "index_close"]]
    out = out.drop_duplicates(subset=["date"]).sort_values("date")
    out["index_ret"] = out["index_close"].pct_change()

    return out


def exclude_unwanted_stocks(df):
    if df.empty:
        return df

    out = df.copy()
    name = out["name"].astype(str)

    spac_mask = name.str.contains(
        "스팩|SPAC|기업인수목적",
        case=False,
        regex=True
    )

    pref_mask = name.str.contains(
        r"우$|우B$|1우|2우|3우|4우|우선주",
        regex=True
    )

    return out[~spac_mask & ~pref_mask].copy()


# ============================================================
# 6. 지표 계산
# ============================================================

def downside_beta(stock_ret, index_ret):
    valid = pd.DataFrame({
        "stock_ret": stock_ret,
        "index_ret": index_ret
    }).dropna()

    if len(valid) < 5:
        return np.nan

    var = np.var(valid["index_ret"], ddof=1)

    if var == 0 or np.isnan(var):
        return np.nan

    cov = np.cov(valid["stock_ret"], valid["index_ret"])[0, 1]
    return cov / var


def calculate_metrics(stock_df, index_df, market_name):
    s = stock_df.copy()
    i = index_df.copy()

    if s.empty or i.empty:
        return pd.DataFrame()

    s = s.sort_values(["code", "date"])
    s["stock_ret"] = s.groupby("code")["close"].pct_change()

    merged = s.merge(
        i[["date", "index_close", "index_ret"]],
        on="date",
        how="inner"
    )

    merged = merged.dropna(subset=["stock_ret", "index_ret"])

    if merged.empty:
        return pd.DataFrame()

    merged["excess_ret"] = merged["stock_ret"] - merged["index_ret"]
    merged["down_day"] = merged["index_ret"] <= DOWN_THRESHOLD
    merged["up_day"] = merged["index_ret"] > 0

    result = []

    for code, g in merged.groupby("code"):
        g = g.sort_values("date")
        gd = g[g["down_day"]].copy()
        gu = g[g["up_day"]].copy()

        if len(g) == 0:
            continue

        latest = g.iloc[-1]

        n_days = len(g)
        n_down = len(gd)
        n_up = len(gu)

        if n_down > 0:
            defense_rate = (gd["stock_ret"] >= gd["index_ret"]).mean()
            positive_on_down_rate = (gd["stock_ret"] > 0).mean()
            avg_excess_down = gd["excess_ret"].mean()
            avg_stock_ret_down = gd["stock_ret"].mean()
            avg_index_ret_down = gd["index_ret"].mean()

            sum_stock_down = gd["stock_ret"].sum()
            sum_index_down = gd["index_ret"].sum()

            downside_capture = (
                np.nan if sum_index_down == 0
                else sum_stock_down / sum_index_down
            )

            down_beta = downside_beta(gd["stock_ret"], gd["index_ret"])
        else:
            defense_rate = np.nan
            positive_on_down_rate = np.nan
            avg_excess_down = np.nan
            avg_stock_ret_down = np.nan
            avg_index_ret_down = np.nan
            downside_capture = np.nan
            down_beta = np.nan

        if n_up > 0:
            up_participation_rate = (gu["stock_ret"] > 0).mean()
            avg_excess_up = gu["excess_ret"].mean()
        else:
            up_participation_rate = np.nan
            avg_excess_up = np.nan

        avg_trdval = g["trade_value"].mean()
        period_avg_excess = g["excess_ret"].mean()

        total_return = latest["close"] / g.iloc[0]["close"] - 1
        index_total_return = latest["index_close"] / g.iloc[0]["index_close"] - 1
        period_excess_return = total_return - index_total_return

        result.append({
            "market": market_name,
            "code": code,
            "name": latest["name"],
            "latest_date": latest["date"],
            "latest_close": latest["close"],

            "n_days": n_days,
            "n_down_days": n_down,
            "n_up_days": n_up,

            "defense_rate": defense_rate,
            "positive_on_down_rate": positive_on_down_rate,
            "avg_excess_down": avg_excess_down,
            "avg_stock_ret_down": avg_stock_ret_down,
            "avg_index_ret_down": avg_index_ret_down,
            "downside_capture": downside_capture,
            "down_beta": down_beta,

            "up_participation_rate": up_participation_rate,
            "avg_excess_up": avg_excess_up,

            "period_avg_excess": period_avg_excess,
            "total_return": total_return,
            "index_total_return": index_total_return,
            "period_excess_return": period_excess_return,

            "avg_trdval": avg_trdval,
        })

    return pd.DataFrame(result)


def build_market_requirements(kospi_index, kosdaq_index):
    req = []

    for market, idx in [
        ("KOSPI", kospi_index),
        ("KOSDAQ", kosdaq_index)
    ]:
        valid = idx.dropna(subset=["index_ret"]).copy()

        required_n_days = len(valid)
        required_n_down_days = int((valid["index_ret"] <= DOWN_THRESHOLD).sum())

        req.append({
            "market": market,
            "required_n_days": required_n_days,
            "required_n_down_days": required_n_down_days,
        })

    return pd.DataFrame(req)


# ============================================================
# 7. 점수화
# ============================================================

def percentile_score(series, higher_is_better=True):
    s = series.copy()
    s = s.replace([np.inf, -np.inf], np.nan)

    rank = s.rank(pct=True, na_option="bottom")

    if higher_is_better:
        return rank
    else:
        return 1 - rank


def add_scores(metrics, market_requirements):
    df = metrics.copy()

    if df.empty:
        return df

    df = df.merge(
        market_requirements,
        on="market",
        how="left"
    )

    df["min_required_n_days"] = np.ceil(
        df["required_n_days"] * MIN_N_DAYS_RATIO
    ).astype(int)

    df["min_required_n_down_days"] = np.ceil(
        df["required_n_down_days"] * MIN_N_DOWN_DAYS_RATIO
    ).astype(int)

    eligible = (
        (df["n_days"] >= df["min_required_n_days"]) &
        (df["n_down_days"] >= df["min_required_n_down_days"]) &
        (df["avg_trdval"] >= MIN_AVG_TRDVAL) &
        (df["avg_excess_down"] > MIN_AVG_EXCESS_DOWN_1ST) &
        (df["defense_rate"] >= MIN_DEFENSE_RATE_1ST) &
        (df["downside_capture"] <= MAX_DOWNSIDE_CAPTURE_1ST)
    )

    df["eligible"] = eligible

    base = df[df["eligible"]].copy()

    if base.empty:
        df["total_score"] = 0
        return df

    base["score_avg_excess_down"] = percentile_score(
        base["avg_excess_down"], True
    ) * 25

    base["score_downside_capture"] = percentile_score(
        base["downside_capture"], False
    ) * 25

    base["score_positive_on_down"] = percentile_score(
        base["positive_on_down_rate"], True
    ) * 20

    base["score_down_beta"] = percentile_score(
        base["down_beta"], False
    ) * 10

    base["score_up_participation"] = percentile_score(
        base["up_participation_rate"], True
    ) * 10

    base["score_liquidity"] = percentile_score(
        base["avg_trdval"], True
    ) * 10

    base["total_score"] = (
        base["score_avg_excess_down"] +
        base["score_downside_capture"] +
        base["score_positive_on_down"] +
        base["score_down_beta"] +
        base["score_up_participation"] +
        base["score_liquidity"]
    )

    score_cols = [
        "score_avg_excess_down",
        "score_downside_capture",
        "score_positive_on_down",
        "score_down_beta",
        "score_up_participation",
        "score_liquidity",
        "total_score",
    ]

    df = df.merge(
        base[["market", "code"] + score_cols],
        on=["market", "code"],
        how="left"
    )

    df["total_score"] = df["total_score"].fillna(0)

    return df


# ============================================================
# 8. 결과 포맷
# ============================================================

def format_result(df):
    out = df.copy()

    pct_cols = [
        "defense_rate",
        "positive_on_down_rate",
        "avg_excess_down",
        "avg_stock_ret_down",
        "avg_index_ret_down",
        "up_participation_rate",
        "avg_excess_up",
        "period_avg_excess",
        "total_return",
        "index_total_return",
        "period_excess_return",
    ]

    for col in pct_cols:
        if col in out.columns:
            out[col] = (out[col] * 100).round(2)

    ratio_cols = [
        "downside_capture",
        "down_beta",
    ]

    for col in ratio_cols:
        if col in out.columns:
            out[col] = out[col].round(3)

    if "avg_trdval" in out.columns:
        out["avg_trdval_억원"] = (out["avg_trdval"] / 100_000_000).round(1)

    if "total_score" in out.columns:
        out["total_score"] = out["total_score"].round(2)

    return out


def make_excel_bytes(filtered_display, top_display, kospi_top_display, kosdaq_top_display):
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        filtered_display.to_excel(writer, sheet_name="조건통과_전체", index=False)
        top_display.to_excel(writer, sheet_name="조건통과_TOP", index=False)
        kospi_top_display.to_excel(writer, sheet_name="KOSPI_TOP", index=False)
        kosdaq_top_display.to_excel(writer, sheet_name="KOSDAQ_TOP", index=False)

    return output.getvalue()


def make_html_table(df, max_rows=20):
    if df.empty:
        return "<p>조건을 통과한 종목이 없습니다.</p>"

    use_cols = [
        "market",
        "code",
        "name",
        "total_score",
        "defense_rate",
        "positive_on_down_rate",
        "avg_excess_down",
        "downside_capture",
        "down_beta",
        "period_excess_return",
        "avg_trdval_억원",
    ]

    available_cols = [c for c in use_cols if c in df.columns]

    show_df = df[available_cols].head(max_rows).copy()

    return show_df.to_html(index=False, border=0)


# ============================================================
# 9. 메일 발송
# ============================================================

def send_email(subject, html_body, attachment_bytes=None, attachment_name=None):
    msg = EmailMessage()
    msg["From"] = MAIL_USERNAME
    msg["To"] = MAIL_TO
    msg["Subject"] = subject

    msg.set_content("HTML 형식의 KRX 스크리닝 리포트입니다.")
    msg.add_alternative(html_body, subtype="html")

    if attachment_bytes is not None and attachment_name is not None:
        msg.add_attachment(
            attachment_bytes,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=attachment_name
        )

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(MAIL_USERNAME, MAIL_APP_PASSWORD)
        smtp.send_message(msg)


# ============================================================
# 10. 메인 실행
# ============================================================

def main():
    print("KRX daily report started")

    kospi_index_raw = fetch_range(
        URLS["KOSPI_INDEX"],
        KOSPI_START_DATE,
        KOSPI_END_DATE,
        desc="KOSPI index"
    )

    kosdaq_index_raw = fetch_range(
        URLS["KOSDAQ_INDEX"],
        KOSDAQ_START_DATE,
        KOSDAQ_END_DATE,
        desc="KOSDAQ index"
    )

    kospi_stock_raw = fetch_range(
        URLS["KOSPI_STOCK"],
        KOSPI_START_DATE,
        KOSPI_END_DATE,
        desc="KOSPI stocks"
    )

    kosdaq_stock_raw = fetch_range(
        URLS["KOSDAQ_STOCK"],
        KOSDAQ_START_DATE,
        KOSDAQ_END_DATE,
        desc="KOSDAQ stocks"
    )

    kospi_index = standardize_index_df(kospi_index_raw, "KOSPI")
    kosdaq_index = standardize_index_df(kosdaq_index_raw, "KOSDAQ")

    kospi_stock = standardize_stock_df(kospi_stock_raw, "KOSPI")
    kosdaq_stock = standardize_stock_df(kosdaq_stock_raw, "KOSDAQ")

    kospi_stock = exclude_unwanted_stocks(kospi_stock)
    kosdaq_stock = exclude_unwanted_stocks(kosdaq_stock)

    market_requirements = build_market_requirements(kospi_index, kosdaq_index)

    kospi_metrics = calculate_metrics(kospi_stock, kospi_index, "KOSPI")
    kosdaq_metrics = calculate_metrics(kosdaq_stock, kosdaq_index, "KOSDAQ")

    all_metrics = pd.concat(
        [kospi_metrics, kosdaq_metrics],
        ignore_index=True
    )

    scored = add_scores(all_metrics, market_requirements)

    strict_filter = (
        (scored["eligible"]) &
        (scored["defense_rate"] >= MIN_DEFENSE_RATE_FINAL) &
        (scored["positive_on_down_rate"] >= MIN_POSITIVE_ON_DOWN_RATE_FINAL) &
        (scored["avg_excess_down"] >= MIN_AVG_EXCESS_DOWN_FINAL) &
        (scored["downside_capture"] <= MAX_DOWNSIDE_CAPTURE_FINAL) &
        (scored["down_beta"] <= MAX_DOWN_BETA_FINAL) &
        (scored["period_excess_return"] > MIN_PERIOD_EXCESS_RETURN_FINAL) &
        (scored["avg_trdval"] >= MIN_AVG_TRDVAL)
    )

    filtered_scored = scored[strict_filter].copy()

    filtered_display = format_result(
        filtered_scored.sort_values("total_score", ascending=False)
    )

    top_display = filtered_display.head(TOP_N)

    kospi_top_display = (
        filtered_display[filtered_display["market"] == "KOSPI"]
        .head(TOP_N)
    )

    kosdaq_top_display = (
        filtered_display[filtered_display["market"] == "KOSDAQ"]
        .head(TOP_N)
    )

    excel_bytes = make_excel_bytes(
        filtered_display,
        top_display,
        kospi_top_display,
        kosdaq_top_display
    )

    report_date = datetime.now(KST).strftime("%Y-%m-%d")

    latest_kospi_date = (
        kospi_index["date"].max().strftime("%Y-%m-%d")
        if not kospi_index.empty
        else "데이터 없음"
    )

    latest_kosdaq_date = (
        kosdaq_index["date"].max().strftime("%Y-%m-%d")
        if not kosdaq_index.empty
        else "데이터 없음"
    )

    subject = f"[KRX 스크리닝 리포트] {report_date} 조건 통과 종목 {len(filtered_display)}개"

    html_body = f"""
    <html>
    <body>
        <h2>KRX 하락장 방어 종목 리포트</h2>

        <p><b>리포트 생성일:</b> {report_date}</p>
        <p><b>KOSPI 조회기간:</b> {KOSPI_START_DATE} ~ {KOSPI_END_DATE}</p>
        <p><b>KOSDAQ 조회기간:</b> {KOSDAQ_START_DATE} ~ {KOSDAQ_END_DATE}</p>
        <p><b>KOSPI 최신 데이터일:</b> {latest_kospi_date}</p>
        <p><b>KOSDAQ 최신 데이터일:</b> {latest_kosdaq_date}</p>

        <h3>요약</h3>
        <ul>
            <li>조건 통과 전체 종목: {len(filtered_display)}개</li>
            <li>KOSPI 조건 통과 종목: {len(kospi_top_display)}개</li>
            <li>KOSDAQ 조건 통과 종목: {len(kosdaq_top_display)}개</li>
            <li>평균 거래대금 기준: {MIN_AVG_TRDVAL_EOK}억 원 이상</li>
            <li>지수 하락일 기준: {DOWN_THRESHOLD * 100:.2f}% 이하</li>
        </ul>

        <h3>전체 TOP {TOP_N}</h3>
        {make_html_table(top_display, TOP_N)}

        <p>전체 결과는 첨부된 엑셀 파일에서 확인하세요.</p>
    </body>
    </html>
    """

    attachment_name = f"KRX_하락장_방어종목_{report_date}.xlsx"

    send_email(
        subject=subject,
        html_body=html_body,
        attachment_bytes=excel_bytes,
        attachment_name=attachment_name
    )

    print("KRX daily report finished")


if __name__ == "__main__":
    main()
