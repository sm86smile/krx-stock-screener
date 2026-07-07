import io
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests
import streamlit as st


# ============================================================
# 0. 페이지 설정
# ============================================================

st.set_page_config(
    page_title="KRX 하락장 방어 종목 스크리너",
    page_icon="📉",
    layout="wide"
)

st.title("📉 KRX 하락장 방어 종목 스크리너")

st.caption(
    "코스피·코스닥 지수 하락일에도 잘 방어하거나 상승한 종목을 "
    "KRX Open API 기반으로 선별합니다."
)


# ============================================================
# 1. KRX API 기본 설정
# ============================================================

API_BASE = "https://data-dbg.krx.co.kr/svc/apis"

URLS = {
    "KOSPI_INDEX": f"{API_BASE}/idx/kospi_dd_trd",
    "KOSDAQ_INDEX": f"{API_BASE}/idx/kosdaq_dd_trd",
    "KOSPI_STOCK": f"{API_BASE}/sto/stk_bydd_trd",
    "KOSDAQ_STOCK": f"{API_BASE}/sto/ksq_bydd_trd",
}

try:
    AUTH_KEY = st.secrets["KRX_AUTH_KEY"]
except Exception:
    AUTH_KEY = ""

if not AUTH_KEY:
    st.error(
        "KRX_AUTH_KEY가 설정되어 있지 않습니다. "
        "Streamlit Cloud의 Secrets에 KRX_AUTH_KEY를 등록하세요."
    )
    st.stop()


# ============================================================
# 2. 사이드바 - 조회 조건
# ============================================================

st.sidebar.header("조회 조건")

today = datetime.today().date()

kospi_start = st.sidebar.date_input(
    "KOSPI 시작일",
    value=pd.to_datetime("2025-01-01").date(),
    help="코스피 종목과 KOSPI 지수를 비교할 분석 시작일입니다."
)

kospi_end = st.sidebar.date_input(
    "KOSPI 종료일",
    value=today,
    help="코스피 종목과 KOSPI 지수를 비교할 분석 종료일입니다."
)

kosdaq_start = st.sidebar.date_input(
    "KOSDAQ 시작일",
    value=pd.to_datetime("2025-01-01").date(),
    help="코스닥 종목과 KOSDAQ 지수를 비교할 분석 시작일입니다."
)

kosdaq_end = st.sidebar.date_input(
    "KOSDAQ 종료일",
    value=today,
    help="코스닥 종목과 KOSDAQ 지수를 비교할 분석 종료일입니다."
)

st.sidebar.divider()

st.sidebar.subheader("지수 하락일 기준")

DOWN_THRESHOLD_PCT = st.sidebar.number_input(
    "지수 하락일 기준, %",
    min_value=-10.0,
    max_value=0.0,
    value=-0.5,
    step=0.1,
    help=(
        "지수 하락일을 정의하는 기준입니다. "
        "예를 들어 -0.5는 지수가 하루에 -0.5% 이하 하락한 날만 "
        "하락일로 계산한다는 의미입니다."
    )
)

DOWN_THRESHOLD = DOWN_THRESHOLD_PCT / 100

TOP_N = st.sidebar.number_input(
    "표시 종목 수",
    min_value=10,
    max_value=300,
    value=50,
    step=10,
    help="최종 조건을 통과한 종목 중 total_score 순으로 몇 개까지 표시할지 정합니다."
)

SLEEP_SEC = 0.03

st.sidebar.divider()


# ============================================================
# 3. 사이드바 - 1차 스크리닝 기준값
# ============================================================

st.sidebar.subheader("1차 스크리닝 기준값")

MIN_N_DAYS_RATIO_PCT = st.sidebar.number_input(
    "n_days 기준 충족률, %",
    min_value=50.0,
    max_value=100.0,
    value=100.0,
    step=1.0,
    help=(
        "코스피/코스닥 각각 입력한 조회기간의 전체 거래일 중 "
        "종목 데이터가 최소 몇 % 이상 있어야 하는지 정합니다. "
        "기본값 100%는 조회기간 전체 거래일을 모두 충족해야 한다는 의미입니다. "
        "예를 들어 95%로 낮추면 일부 거래일 데이터가 없는 종목도 포함될 수 있습니다."
    )
)

MIN_N_DOWN_DAYS_RATIO_PCT = st.sidebar.number_input(
    "n_down_days 기준 충족률, %",
    min_value=50.0,
    max_value=100.0,
    value=100.0,
    step=1.0,
    help=(
        "코스피/코스닥 각각 입력한 조회기간 내 지수 하락일 중 "
        "종목 데이터가 최소 몇 % 이상 있어야 하는지 정합니다. "
        "기본값 100%는 조회기간 내 지수 하락일 전체를 모두 충족해야 한다는 의미입니다."
    )
)

MIN_AVG_TRDVAL_EOK = st.sidebar.number_input(
    "avg_trdval 기준, 억 원",
    min_value=0.0,
    max_value=10000.0,
    value=50.0,
    step=10.0,
    help=(
        "조회기간 동안의 평균 거래대금 기준입니다. "
        "기본값 50억 원입니다. "
        "거래대금이 너무 작은 종목은 실제 매매가 어렵거나 가격 왜곡이 있을 수 있어 제외합니다."
    )
)

MIN_AVG_EXCESS_DOWN_1ST_PCT = st.sidebar.number_input(
    "1차 avg_excess_down 기준, %",
    min_value=-10.0,
    max_value=10.0,
    value=0.0,
    step=0.1,
    help=(
        "지수 하락일 평균 초과수익률 기준입니다. "
        "기본값 0은 지수 하락일에 평균적으로 지수보다 강한 종목만 남긴다는 의미입니다. "
        "예를 들어 0.3으로 입력하면 +0.3%p 이상 강한 종목만 남깁니다."
    )
)

MIN_DEFENSE_RATE_1ST_PCT = st.sidebar.number_input(
    "1차 defense_rate 기준, %",
    min_value=0.0,
    max_value=100.0,
    value=55.0,
    step=1.0,
    help=(
        "지수 하락일 중 종목이 지수보다 덜 빠지거나 더 오른 날의 비율입니다. "
        "기본값 55%는 지수 하락일의 55% 이상에서 지수보다 강했던 종목만 남긴다는 의미입니다."
    )
)

MAX_DOWNSIDE_CAPTURE_1ST = st.sidebar.number_input(
    "1차 downside_capture 기준",
    min_value=-5.0,
    max_value=5.0,
    value=1.0,
    step=0.1,
    help=(
        "지수 하락폭 대비 종목이 얼마나 하락했는지 보는 지표입니다. "
        "기본값 1.0 이하는 지수보다 덜 빠진 종목을 의미합니다. "
        "0에 가까울수록 방어력이 좋고, 음수면 지수 하락기에 오히려 상승했다는 의미입니다."
    )
)

st.sidebar.divider()


# ============================================================
# 4. 사이드바 - 최종 스크리닝 기준값
# ============================================================

st.sidebar.subheader("최종 스크리닝 기준값")

MIN_DEFENSE_RATE_FINAL_PCT = st.sidebar.number_input(
    "최종 defense_rate 기준, %",
    min_value=0.0,
    max_value=100.0,
    value=60.0,
    step=1.0,
    help=(
        "최종 필터에서 적용할 defense_rate 기준입니다. "
        "기본값 60%는 지수 하락일 중 60% 이상에서 지수보다 강했던 종목만 남깁니다."
    )
)

MIN_POSITIVE_ON_DOWN_RATE_FINAL_PCT = st.sidebar.number_input(
    "positive_on_down_rate 기준, %",
    min_value=0.0,
    max_value=100.0,
    value=25.0,
    step=1.0,
    help=(
        "지수 하락일 중 종목 수익률이 플러스였던 날의 비율입니다. "
        "기본값 25%는 지수가 하락한 날 4번 중 1번 이상은 종목이 상승했다는 의미입니다."
    )
)

MIN_AVG_EXCESS_DOWN_FINAL_PCT = st.sidebar.number_input(
    "최종 avg_excess_down 기준, %",
    min_value=-10.0,
    max_value=10.0,
    value=0.5,
    step=0.1,
    help=(
        "최종 필터에서 적용할 지수 하락일 평균 초과수익률 기준입니다. "
        "기본값 0.5는 지수 하락일 평균 초과수익률이 +0.5%p 이상인 종목만 남긴다는 의미입니다."
    )
)

MAX_DOWNSIDE_CAPTURE_FINAL = st.sidebar.number_input(
    "최종 downside_capture 기준",
    min_value=-5.0,
    max_value=5.0,
    value=0.8,
    step=0.1,
    help=(
        "최종 필터에서 적용할 downside_capture 기준입니다. "
        "기본값 0.8 이하는 지수 하락폭의 80% 이하만 하락한 종목을 의미합니다."
    )
)

MAX_DOWN_BETA_FINAL = st.sidebar.number_input(
    "down_beta 기준",
    min_value=-5.0,
    max_value=5.0,
    value=0.8,
    step=0.1,
    help=(
        "지수 하락일만 따로 계산한 베타입니다. "
        "기본값 0.8 이하는 하락장에서 지수 변동에 덜 민감한 종목으로 봅니다."
    )
)

MIN_PERIOD_EXCESS_RETURN_FINAL_PCT = st.sidebar.number_input(
    "period_excess_return 기준, %",
    min_value=-50.0,
    max_value=100.0,
    value=0.0,
    step=0.5,
    help=(
        "코스피/코스닥 각각 입력한 조회기간 전체에서 "
        "종목 누적수익률이 지수 누적수익률보다 얼마나 더 좋아야 하는지 정합니다. "
        "기본값 0은 조회기간 전체 기준으로 지수를 이긴 종목만 남긴다는 의미입니다."
    )
)

st.sidebar.info(
    "recent_60_excess 조건은 사용하지 않습니다. "
    "대신 조회기간 전체 상대강도인 period_excess_return을 사용합니다."
)

st.sidebar.divider()

run_button = st.sidebar.button("분석 실행", type="primary")


# ============================================================
# 5. 내부 계산용 기준값 변환
# ============================================================

MIN_N_DAYS_RATIO = MIN_N_DAYS_RATIO_PCT / 100
MIN_N_DOWN_DAYS_RATIO = MIN_N_DOWN_DAYS_RATIO_PCT / 100

MIN_AVG_TRDVAL = MIN_AVG_TRDVAL_EOK * 100_000_000

MIN_AVG_EXCESS_DOWN_1ST = MIN_AVG_EXCESS_DOWN_1ST_PCT / 100
MIN_DEFENSE_RATE_1ST = MIN_DEFENSE_RATE_1ST_PCT / 100

MIN_DEFENSE_RATE_FINAL = MIN_DEFENSE_RATE_FINAL_PCT / 100
MIN_POSITIVE_ON_DOWN_RATE_FINAL = MIN_POSITIVE_ON_DOWN_RATE_FINAL_PCT / 100
MIN_AVG_EXCESS_DOWN_FINAL = MIN_AVG_EXCESS_DOWN_FINAL_PCT / 100
MIN_PERIOD_EXCESS_RETURN_FINAL = MIN_PERIOD_EXCESS_RETURN_FINAL_PCT / 100


# ============================================================
# 6. 공통 함수
# ============================================================

def yyyymmdd(date_value):
    return pd.to_datetime(date_value).strftime("%Y%m%d")


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


def krx_get(url, bas_dd, auth_key, sleep_sec=SLEEP_SEC):
    headers = {"AUTH_KEY": auth_key}
    params = {"basDd": bas_dd}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=20)
        res.raise_for_status()
        rows = extract_rows(res.json())
        time.sleep(sleep_sec)
        return pd.DataFrame(rows)

    except Exception:
        time.sleep(sleep_sec)
        return pd.DataFrame()


def make_bdate_list(start_date, end_date):
    dates = pd.bdate_range(
        pd.to_datetime(start_date),
        pd.to_datetime(end_date)
    )
    return [d.strftime("%Y%m%d") for d in dates]


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_range_cached(url, start_date, end_date, _auth_key):
    date_list = make_bdate_list(start_date, end_date)
    result = []

    for d in date_list:
        df = krx_get(url, d, _auth_key)

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
# 7. 데이터 표준화 함수
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
        raise ValueError(
            f"종목 데이터 필수 컬럼 누락: {missing}\n"
            f"현재 컬럼: {df.columns.tolist()}"
        )

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
        raise ValueError(
            f"지수 데이터 필수 컬럼 누락: {missing}\n"
            f"현재 컬럼: {df.columns.tolist()}"
        )

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
# 8. 지표 계산 함수
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
# 9. 점수화 함수
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
# 10. 결과 포맷 함수
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


def show_current_conditions():
    st.markdown(
        f"""
        ### 1차 스크리닝

        - `n_days`: 조회기간 전체 거래일의 **{MIN_N_DAYS_RATIO_PCT:.0f}% 이상**
        - `n_down_days`: 조회기간 내 지수 하락일의 **{MIN_N_DOWN_DAYS_RATIO_PCT:.0f}% 이상**
        - `avg_trdval`: **{MIN_AVG_TRDVAL_EOK:.0f}억 원 이상**
        - `avg_excess_down`: **{MIN_AVG_EXCESS_DOWN_1ST_PCT:.2f}% 초과**
        - `defense_rate`: **{MIN_DEFENSE_RATE_1ST_PCT:.0f}% 이상**
        - `downside_capture`: **{MAX_DOWNSIDE_CAPTURE_1ST:.2f} 이하**

        ### 최종 스크리닝

        - `defense_rate`: **{MIN_DEFENSE_RATE_FINAL_PCT:.0f}% 이상**
        - `positive_on_down_rate`: **{MIN_POSITIVE_ON_DOWN_RATE_FINAL_PCT:.0f}% 이상**
        - `avg_excess_down`: **{MIN_AVG_EXCESS_DOWN_FINAL_PCT:.2f}% 이상**
        - `downside_capture`: **{MAX_DOWNSIDE_CAPTURE_FINAL:.2f} 이하**
        - `down_beta`: **{MAX_DOWN_BETA_FINAL:.2f} 이하**
        - `period_excess_return`: **{MIN_PERIOD_EXCESS_RETURN_FINAL_PCT:.2f}% 초과**
        - `recent_60_excess`: 조건 삭제
        - `avg_trdval_억원`: **{MIN_AVG_TRDVAL_EOK:.0f}억 원 이상**

        ### 랭킹 기준

        최종 조건을 통과한 종목만 `total_score` 높은 순서로 정렬합니다.

        - 하락일 평균 초과수익률: 25점
        - Downside Capture: 25점
        - 하락일 상승 비율: 20점
        - 하락 베타: 10점
        - 상승장 참여율: 10점
        - 평균 거래대금: 10점
        """
    )


# ============================================================
# 11. 실행 전 안내
# ============================================================

if not run_button:
    st.info("왼쪽 사이드바에서 조회기간과 스크리닝 기준값을 확인한 뒤, **분석 실행**을 누르세요.")

    with st.expander("현재 적용된 스크리닝 조건 보기", expanded=True):
        show_current_conditions()

    st.stop()


# ============================================================
# 12. 실행
# ============================================================

if kospi_start > kospi_end:
    st.error("KOSPI 시작일이 종료일보다 늦습니다.")
    st.stop()

if kosdaq_start > kosdaq_end:
    st.error("KOSDAQ 시작일이 종료일보다 늦습니다.")
    st.stop()

KOSPI_START_DATE = yyyymmdd(kospi_start)
KOSPI_END_DATE = yyyymmdd(kospi_end)
KOSDAQ_START_DATE = yyyymmdd(kosdaq_start)
KOSDAQ_END_DATE = yyyymmdd(kosdaq_end)

with st.spinner("KRX 데이터를 수집하고 분석 중입니다. 조회기간이 길면 시간이 걸릴 수 있습니다."):
    st.subheader("1. 데이터 수집")

    col1, col2 = st.columns(2)

    with col1:
        st.write("KOSPI 데이터 수집")
        kospi_index_raw = fetch_range_cached(
            URLS["KOSPI_INDEX"],
            KOSPI_START_DATE,
            KOSPI_END_DATE,
            AUTH_KEY
        )

        kospi_stock_raw = fetch_range_cached(
            URLS["KOSPI_STOCK"],
            KOSPI_START_DATE,
            KOSPI_END_DATE,
            AUTH_KEY
        )

    with col2:
        st.write("KOSDAQ 데이터 수집")
        kosdaq_index_raw = fetch_range_cached(
            URLS["KOSDAQ_INDEX"],
            KOSDAQ_START_DATE,
            KOSDAQ_END_DATE,
            AUTH_KEY
        )

        kosdaq_stock_raw = fetch_range_cached(
            URLS["KOSDAQ_STOCK"],
            KOSDAQ_START_DATE,
            KOSDAQ_END_DATE,
            AUTH_KEY
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

    top_candidates = (
        filtered_scored
        .sort_values("total_score", ascending=False)
        .head(int(TOP_N))
    )

    kospi_top = (
        filtered_scored[filtered_scored["market"] == "KOSPI"]
        .sort_values("total_score", ascending=False)
        .head(int(TOP_N))
    )

    kosdaq_top = (
        filtered_scored[filtered_scored["market"] == "KOSDAQ"]
        .sort_values("total_score", ascending=False)
        .head(int(TOP_N))
    )

    filtered_display = format_result(
        filtered_scored.sort_values("total_score", ascending=False)
    )

    top_display = format_result(top_candidates)
    kospi_top_display = format_result(kospi_top)
    kosdaq_top_display = format_result(kosdaq_top)


# ============================================================
# 13. 화면 출력
# ============================================================

st.subheader("2. 분석 요약")

summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)

summary_col1.metric("조건 통과 종목", f"{len(filtered_scored):,}개")
summary_col2.metric("KOSPI TOP 표시", f"{len(kospi_top):,}개")
summary_col3.metric("KOSDAQ TOP 표시", f"{len(kosdaq_top):,}개")
summary_col4.metric("평균 거래대금 기준", f"{MIN_AVG_TRDVAL_EOK:,.0f}억 원")

st.write("시장별 조회기간 요구 조건")

st.dataframe(
    market_requirements,
    use_container_width=True
)

with st.expander("현재 적용된 스크리닝 조건 보기", expanded=False):
    show_current_conditions()

final_cols = [
    "market",
    "code",
    "name",
    "latest_date",
    "latest_close",
    "total_score",

    "n_days",
    "required_n_days",
    "min_required_n_days",

    "n_down_days",
    "required_n_down_days",
    "min_required_n_down_days",

    "defense_rate",
    "positive_on_down_rate",
    "avg_excess_down",
    "downside_capture",
    "down_beta",

    "up_participation_rate",
    "avg_excess_up",

    "period_avg_excess",
    "total_return",
    "index_total_return",
    "period_excess_return",

    "avg_trdval_억원",
]

available_cols = [
    col for col in final_cols
    if col in filtered_display.columns
]

if len(filtered_scored) == 0:
    st.warning(
        "조건을 만족하는 종목이 없습니다. "
        "사이드바에서 조건을 완화하거나 조회기간을 조정해보세요."
    )

    st.markdown(
        """
        조건 완화 예시:

        - `n_days 기준 충족률`: 100 → 95
        - `n_down_days 기준 충족률`: 100 → 95
        - `최종 avg_excess_down`: 0.5 → 0.3
        - `최종 downside_capture`: 0.8 → 1.0
        - `down_beta`: 0.8 → 1.0
        - `avg_trdval`: 50억 → 30억
        """
    )
else:
    tab1, tab2, tab3, tab4 = st.tabs(
        ["전체 조건통과", "전체 TOP", "KOSPI TOP", "KOSDAQ TOP"]
    )

    with tab1:
        st.dataframe(
            filtered_display[available_cols],
            use_container_width=True
        )

    with tab2:
        st.dataframe(
            top_display[available_cols],
            use_container_width=True
        )

    with tab3:
        st.dataframe(
            kospi_top_display[available_cols],
            use_container_width=True
        )

    with tab4:
        st.dataframe(
            kosdaq_top_display[available_cols],
            use_container_width=True
        )

    excel_bytes = make_excel_bytes(
        filtered_display,
        top_display,
        kospi_top_display,
        kosdaq_top_display
    )

    file_name = (
        f"KRX_하락장_방어종목_"
        f"KOSPI_{KOSPI_START_DATE}_{KOSPI_END_DATE}_"
        f"KOSDAQ_{KOSDAQ_START_DATE}_{KOSDAQ_END_DATE}.xlsx"
    )

    st.download_button(
        label="📥 엑셀 다운로드",
        data=excel_bytes,
        file_name=file_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
