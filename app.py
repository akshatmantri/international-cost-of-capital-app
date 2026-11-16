"""
Foreign Cost of Capital — USD vs. local-currency cost of equity and WACC.

Method (Rebelo, "Designing cost of capital app"):
    D/E        = d / (1 - d)
    beta_E     = beta_U * [1 + (1 - tau) * D/E]
    Re         = Rf + beta_E * ERP                              (USD, CAPM)
    Rd         = Rf + s                                         (USD)
    F          = (1 + Rf*) / (1 + Rf)                           (currency conversion factor)
    1 + Re*    = (1 + Re) * F                                   (local currency)
    s*         = s * F       (default; user may override)
    Rd*        = Rf* + s*
    WACC       = (1 - d) * Re  + d * Rd  * (1 - tau)
    WACC*      = (1 - d) * Re* + d * Rd* * (1 - tau)

Data sources (fetched live, cached, with fallbacks and manual overrides):
    * Unlevered industry betas – Damodaran, betas.xls (U.S. industries)
    * BBB default spread       – Damodaran, ratings.html
    * Corporate tax rates      – OECD Corporate Tax Statistics (combined statutory CIT rate)
    * 10-year govt bond yields – Trading Economics (scraped)
"""

from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

BETAS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html#chapter-d1e2342-08e74a33fb"
)
# The OECD web page is rendered client-side behind a bot challenge; the same
# statistics are served by the OECD's official SDMX data API (dataflow DF_CIT).
OECD_SDMX_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2020&dimensionAtObservation=AllDimensions"
)
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 20
CACHE_TTL = 6 * 60 * 60  # 6 hours

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
DEFAULT_ERP = 6.5
DEFAULT_DEBT_SHARE = 30
US = "United States"

# name -> (ISO3 for OECD, Trading Economics slug, currency code)
COUNTRIES: dict[str, tuple[str, str, str]] = {
    "Australia": ("AUS", "australia", "AUD"),
    "Austria": ("AUT", "austria", "EUR"),
    "Belgium": ("BEL", "belgium", "EUR"),
    "Brazil": ("BRA", "brazil", "BRL"),
    "Canada": ("CAN", "canada", "CAD"),
    "Chile": ("CHL", "chile", "CLP"),
    "China": ("CHN", "china", "CNY"),
    "Colombia": ("COL", "colombia", "COP"),
    "Croatia": ("HRV", "croatia", "EUR"),
    "Czech Republic": ("CZE", "czech-republic", "CZK"),
    "Denmark": ("DNK", "denmark", "DKK"),
    "Finland": ("FIN", "finland", "EUR"),
    "France": ("FRA", "france", "EUR"),
    "Germany": ("DEU", "germany", "EUR"),
    "Greece": ("GRC", "greece", "EUR"),
    "Hong Kong": ("HKG", "hong-kong", "HKD"),
    "Hungary": ("HUN", "hungary", "HUF"),
    "Iceland": ("ISL", "iceland", "ISK"),
    "India": ("IND", "india", "INR"),
    "Indonesia": ("IDN", "indonesia", "IDR"),
    "Ireland": ("IRL", "ireland", "EUR"),
    "Israel": ("ISR", "israel", "ILS"),
    "Italy": ("ITA", "italy", "EUR"),
    "Japan": ("JPN", "japan", "JPY"),
    "Kenya": ("KEN", "kenya", "KES"),
    "Lithuania": ("LTU", "lithuania", "EUR"),
    "Malaysia": ("MYS", "malaysia", "MYR"),
    "Mexico": ("MEX", "mexico", "MXN"),
    "Netherlands": ("NLD", "netherlands", "EUR"),
    "New Zealand": ("NZL", "new-zealand", "NZD"),
    "Nigeria": ("NGA", "nigeria", "NGN"),
    "Norway": ("NOR", "norway", "NOK"),
    "Pakistan": ("PAK", "pakistan", "PKR"),
    "Peru": ("PER", "peru", "PEN"),
    "Philippines": ("PHL", "philippines", "PHP"),
    "Poland": ("POL", "poland", "PLN"),
    "Portugal": ("PRT", "portugal", "EUR"),
    "Romania": ("ROU", "romania", "RON"),
    "Russia": ("RUS", "russia", "RUB"),
    "Singapore": ("SGP", "singapore", "SGD"),
    "Slovakia": ("SVK", "slovakia", "EUR"),
    "Slovenia": ("SVN", "slovenia", "EUR"),
    "South Africa": ("ZAF", "south-africa", "ZAR"),
    "South Korea": ("KOR", "south-korea", "KRW"),
    "Spain": ("ESP", "spain", "EUR"),
    "Sweden": ("SWE", "sweden", "SEK"),
    "Switzerland": ("CHE", "switzerland", "CHF"),
    "Taiwan": ("TWN", "taiwan", "TWD"),
    "Thailand": ("THA", "thailand", "THB"),
    "Turkey": ("TUR", "turkey", "TRY"),
    "United Kingdom": ("GBR", "united-kingdom", "GBP"),
    "United States": ("USA", "united-states", "USD"),
    "Vietnam": ("VNM", "vietnam", "VND"),
    "Zambia": ("ZMB", "zambia", "ZMW"),
}

# Last-resort snapshots, used only if a live fetch fails (clearly flagged in the UI).
SNAPSHOT_DATE = "15 Nov 2026"
SNAPSHOT_YIELDS = {  # Trading Economics 10Y yields, percent
    "united-states": 5.144, "united-kingdom": 5.3377, "japan": 2.878, "australia": 5.139,
    "germany": 3.3762, "brazil": 15.335, "russia": 16.74, "india": 7.312, "canada": 3.856,
    "italy": 4.437, "france": 4.921, "south-africa": 8.675, "china": 1.6351,
    "switzerland": 0.563, "mexico": 9.308, "netherlands": 3.477, "new-zealand": 4.773,
    "portugal": 3.819, "south-korea": 3.992, "spain": 3.944, "greece": 4.264,
    "turkey": 33.52, "finland": 3.761, "lithuania": 4.325, "norway": 4.384, "poland": 6.335,
    "romania": 7.12, "slovakia": 4.31, "slovenia": 3.914, "sweden": 3.098, "hungary": 5.525,
    "iceland": 6.81, "ireland": 3.539, "austria": 3.61, "belgium": 4.355, "croatia": 3.867,
    "czech-republic": 5.222, "denmark": 3.169, "peru": 6.2414, "chile": 6.01,
    "colombia": 12.699, "singapore": 2.323, "taiwan": 1.86, "thailand": 1.995,
    "vietnam": 4.5726, "hong-kong": 3.9941, "indonesia": 7.607, "israel": 4.183,
    "malaysia": 3.8647, "pakistan": 12.604, "philippines": 7.767, "zambia": 14.74,
    "kenya": 12.5, "nigeria": 16.598,
}
SNAPSHOT_TAX = {  # OECD combined statutory CIT rate, percent (RUS/TWN: not in OECD data)
    "USA": 25.53, "GBR": 25.0, "JPN": 29.74, "AUS": 30.0, "DEU": 30.13, "BRA": 34.0,
    "RUS": 25.0, "IND": 25.17, "CAN": 26.0, "ITA": 27.81, "FRA": 36.13, "ZAF": 27.0,
    "CHN": 25.0, "CHE": 19.47, "MEX": 30.0, "NLD": 25.8, "NZL": 28.0, "PRT": 29.5,
    "KOR": 27.5, "ESP": 25.0, "GRC": 22.0, "TUR": 25.0, "FIN": 20.0, "LTU": 17.0,
    "NOR": 22.0, "POL": 19.0, "ROU": 16.0, "SVK": 24.0, "SVN": 22.0, "SWE": 20.6,
    "HUN": 9.0, "ISL": 20.0, "IRL": 12.5, "AUT": 23.0, "BEL": 25.0, "HRV": 18.0,
    "CZE": 21.0, "DNK": 22.0, "PER": 29.5, "CHL": 27.0, "COL": 35.0, "SGP": 17.0,
    "TWN": 20.0, "THA": 20.0, "VNM": 20.0, "HKG": 16.5, "IDN": 22.0, "ISR": 23.0,
    "MYS": 24.0, "PAK": 29.0, "PHL": 25.0, "ZMB": 30.0, "KEN": 30.0, "NGA": 30.0,
}
SNAPSHOT_BBB_SPREAD = 1.11  # percent, Damodaran Baa2/BBB, large non-financial firms

BETA_MEASURES = {
    "Unlevered beta corrected for cash": "unlev_beta_cash",
    "Unlevered beta": "unlev_beta",
}


# ----------------------------------------------------------------------------
# Data fetching
# ----------------------------------------------------------------------------

def _get(url: str, **kwargs) -> requests.Response:
    resp = requests.get(url, headers=HTTP_HEADERS, timeout=TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


@st.cache_data(ttl=CACHE_TTL, show_spinner="Fetching industry betas (Damodaran)…")
def fetch_betas() -> tuple[pd.DataFrame | None, dict]:
    """Damodaran's U.S. industry betas. Returns (dataframe, meta)."""
    try:
        raw = pd.read_excel(
            io.BytesIO(_get(BETAS_URL).content),
            sheet_name="Industry Averages",
            header=None,
            engine="xlrd",
        )
        hdr_idx = raw.index[raw[0].astype(str).str.strip() == "Industry Name"][0]
        header = [str(c).strip() for c in raw.iloc[hdr_idx]]
        df = raw.iloc[hdr_idx + 1:].copy()
        df.columns = header
        df = df[df["Industry Name"].notna()]
        out = pd.DataFrame({
            "Industry": df["Industry Name"].astype(str).str.strip(),
            "Number of firms": pd.to_numeric(df["Number of firms"], errors="coerce"),
            "Levered beta (market)": pd.to_numeric(df["Beta"], errors="coerce"),
            "D/E ratio (market)": pd.to_numeric(df["D/E Ratio"], errors="coerce"),
            "unlev_beta": pd.to_numeric(df["Unlevered beta"], errors="coerce"),
            "unlev_beta_cash": pd.to_numeric(df["Unlevered beta corrected for cash"], errors="coerce"),
        }).dropna(subset=["unlev_beta"]).reset_index(drop=True)
        updated = raw.iloc[0, 1]
        updated = updated.strftime("%d %b %Y") if hasattr(updated, "strftime") else str(updated)
        return out, {"status": "live", "source": "Damodaran betas.xls", "as_of": f"updated {updated}",
                     "fetched": _now()}
    except Exception as exc:  # noqa: BLE001
        return None, {"status": "failed", "source": "Damodaran betas.xls", "error": str(exc)}


@st.cache_data(ttl=CACHE_TTL, show_spinner="Fetching BBB spread (Damodaran)…")
def fetch_bbb_spread() -> tuple[float, dict]:
    """BBB (Baa2) default spread for large non-financial firms, in percent."""
    try:
        soup = BeautifulSoup(_get(RATINGS_URL).text, "lxml")
        for tr in soup.find_all("tr"):
            cells = [" ".join(c.get_text(" ", strip=True).split()) for c in tr.find_all(["td", "th"])]
            for i, cell in enumerate(cells[:-1]):
                # The first BBB row/column belongs to the "large non-financial firms" table
                if re.fullmatch(r"Baa2?\s*/\s*BBB", cell):
                    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", cells[i + 1])
                    if m:
                        return float(m.group(1)), {"status": "live", "source": "Damodaran ratings.html",
                                                   "as_of": "Baa2/BBB, large non-financial firms",
                                                   "fetched": _now()}
        raise ValueError("BBB row not found")
    except Exception as exc:  # noqa: BLE001
        return SNAPSHOT_BBB_SPREAD, {"status": "fallback", "source": "Damodaran ratings.html",
                                     "as_of": f"snapshot {SNAPSHOT_DATE}", "error": str(exc)}


@st.cache_data(ttl=CACHE_TTL, show_spinner="Fetching corporate tax rates (OECD)…")
def fetch_tax_rates() -> tuple[dict, dict]:
    """OECD combined (central + sub-central) statutory CIT rates: {ISO3: (rate %, year)}."""
    try:
        resp = requests.get(
            OECD_SDMX_URL,
            headers={**HTTP_HEADERS, "Accept": "application/vnd.sdmx.data+csv; charset=utf-8"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        df = df[df["MEASURE"] == "CIT_C"].copy()
        df["OBS_VALUE"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce")
        df = df.dropna(subset=["OBS_VALUE"])
        # Prefer the standard rate (ST); fall back to targeted (TA) if that is all there is.
        df["pref"] = (df["TARGETING"] == "ST").astype(int)
        df = df.sort_values(["REF_AREA", "TIME_PERIOD", "pref"])
        latest = df.groupby("REF_AREA").last()
        rates = {iso: (float(r["OBS_VALUE"]), int(r["TIME_PERIOD"])) for iso, r in latest.iterrows()}
        if "USA" not in rates:
            raise ValueError("OECD response missing USA")
        return rates, {"status": "live", "source": "OECD Corporate Tax Statistics (SDMX API)",
                       "as_of": "combined statutory CIT rate, latest year", "fetched": _now()}
    except Exception as exc:  # noqa: BLE001
        return ({k: (v, None) for k, v in SNAPSHOT_TAX.items()},
                {"status": "fallback", "source": "OECD Corporate Tax Statistics",
                 "as_of": f"snapshot {SNAPSHOT_DATE}", "error": str(exc)})


def _parse_float(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(m.group()) if m else None


@st.cache_data(ttl=CACHE_TTL, show_spinner="Scraping 10-year yields (Trading Economics)…")
def fetch_bonds_table() -> dict:
    """Scrape the Trading Economics /bonds table: {slug: yield %}. Empty dict if blocked."""
    yields: dict[str, float] = {}
    for url in (TE_BONDS_URL, TE_BONDS_URL + "?partial=markets"):
        try:
            resp = requests.get(url, headers={**HTTP_HEADERS, "Referer": TE_BONDS_URL}, timeout=TIMEOUT)
            if resp.status_code != 200 or not resp.text:
                continue
            soup = BeautifulSoup(resp.text, "lxml")
            for tr in soup.find_all("tr"):
                a = tr.find("a", href=re.compile(r"/[a-z-]+/government-bond-yield"))
                if not a:
                    continue
                slug = re.search(r"/([a-z-]+)/government-bond-yield", a["href"]).group(1)
                tds = tr.find_all("td")
                # first numeric cell after the country-name cell is the yield
                name_idx = next((i for i, td in enumerate(tds) if td.find("a")), 0)
                for td in tds[name_idx + 1:]:
                    val = _parse_float(td.get_text(strip=True))
                    if val is not None:
                        yields.setdefault(slug, val)
                        break
            if yields:
                return yields
        except Exception:  # noqa: BLE001
            continue
    return yields


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_country_yield(slug: str) -> float | None:
    """Scrape one Trading Economics country page for the latest 10Y yield."""
    try:
        html = _get(TE_COUNTRY_URL.format(slug=slug)).text
        m = re.search(r"TEChartsMeta\s*=\s*(\[.*?\]);", html, re.S)
        if m:
            meta = json.loads(m.group(1))
            if meta and meta[0].get("last") is not None:
                return float(meta[0]["last"])
        m = re.search(r"Yield[^.<]{0,60}?at\s+(-?\d+(?:\.\d+)?)%", html)
        if m:
            return float(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    return None


def get_yield(country: str) -> tuple[float, dict]:
    """10-year yield (percent) for a country: /bonds table → country page → snapshot."""
    slug = COUNTRIES[country][1]
    table = fetch_bonds_table()
    if slug in table:
        return table[slug], {"status": "live", "source": "Trading Economics /bonds", "fetched": _now()}
    val = fetch_country_yield(slug)
    if val is not None:
        return val, {"status": "live", "source": f"Trading Economics /{slug}/government-bond-yield",
                     "fetched": _now()}
    return SNAPSHOT_YIELDS[slug], {"status": "fallback", "source": "Trading Economics",
                                   "as_of": f"snapshot {SNAPSHOT_DATE}"}


def get_tax(country: str, rates: dict) -> tuple[float, str]:
    iso = COUNTRIES[country][0]
    if iso in rates:
        rate, year = rates[iso]
        return rate, (f"OECD {year}" if year else f"OECD snapshot {SNAPSHOT_DATE}")
    return SNAPSHOT_TAX[iso], "not covered by OECD data — indicative, please verify"


# ----------------------------------------------------------------------------
# Calculations (all rates as decimals)
# ----------------------------------------------------------------------------

def cost_of_capital(beta_u, d, tax, erp, rf_usd, rf_fx, s_usd, s_fx_override=None) -> dict:
    """Works on floats or pandas Series (beta_u) alike."""
    de = d / (1 - d)
    beta_e = beta_u * (1 + (1 - tax) * de)
    factor = (1 + rf_fx) / (1 + rf_usd)

    re_usd = rf_usd + beta_e * erp
    rd_usd = rf_usd + s_usd
    rd_usd_at = rd_usd * (1 - tax)
    wacc_usd = (1 - d) * re_usd + d * rd_usd_at

    re_fx = (1 + re_usd) * factor - 1
    s_fx_auto = s_usd * factor
    s_fx = s_fx_auto if s_fx_override is None else s_fx_override
    rd_fx = rf_fx + s_fx
    rd_fx_at = rd_fx * (1 - tax)
    wacc_fx = (1 - d) * re_fx + d * rd_fx_at

    return dict(de=de, beta_e=beta_e, factor=factor,
                re_usd=re_usd, rd_usd=rd_usd, rd_usd_at=rd_usd_at, wacc_usd=wacc_usd,
                re_fx=re_fx, s_fx_auto=s_fx_auto, s_fx=s_fx, rd_fx=rd_fx, rd_fx_at=rd_fx_at,
                wacc_fx=wacc_fx)


def pct(x: float, dp: int = 2) -> str:
    return f"{x * 100:.{dp}f}%"


# ----------------------------------------------------------------------------
# UI helpers
# ----------------------------------------------------------------------------

def override_input(label: str, fetched: float, key: str, *, fmt: str = "%.2f", step: float = 0.05,
                   min_value: float = -5.0, max_value: float = 100.0, help_text: str | None = None,
                   suffix: str = "%") -> tuple[float, bool]:
    """Toggle + number input. Returns (value used, overridden?)."""
    on = st.toggle(f"Override {label}", key=f"ovr_{key}", help=help_text)
    if on:
        val = st.number_input(f"{label} ({suffix})" if suffix else label, value=float(round(fetched, 4)),
                              min_value=min_value, max_value=max_value, step=step, format=fmt,
                              key=f"val_{key}")
        return float(val), True
    shown = f"{fetched:.2f}{suffix}" if suffix else f"{fetched:.3f}"
    st.caption(f"{label}: **{shown}** (fetched)")
    return float(fetched), False


def status_badge(meta: dict, overridden: bool = False) -> str:
    if overridden:
        return ":violet[● manual]"
    return {"live": ":green[● live]", "fallback": ":orange[● snapshot]",
            "failed": ":red[● unavailable]"}.get(meta.get("status"), "")


def to_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, frame in sheets.items():
            frame.to_excel(xw, sheet_name=name[:31], index=False)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------

st.set_page_config(page_title="Foreign Cost of Capital", page_icon="🌍", layout="wide")

st.title("🌍 Foreign Cost of Capital")
st.caption("Project cost of equity and WACC in USD and in the target country's currency — "
           "CAPM with U.S. industry betas, converted via relative interest rates.")

betas_df, betas_meta = fetch_betas()
bbb_spread, bbb_meta = fetch_bbb_spread()
tax_rates, tax_meta = fetch_tax_rates()

# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Project")

    if betas_df is not None:
        industries = betas_df["Industry"].tolist()
        default_idx = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
        industry = st.selectbox("Industry", industries, index=default_idx)
    else:
        industry = "Custom industry"
        st.error("Could not load Damodaran betas — enter the unlevered beta manually below.")

    countries = sorted(COUNTRIES)
    country = st.selectbox("Target country", countries, index=countries.index(DEFAULT_COUNTRY))
    ccy = COUNTRIES[country][2]

    st.header("Assumptions")
    erp_pct = st.number_input("Equity risk premium (%)", min_value=0.0, max_value=20.0,
                              value=DEFAULT_ERP, step=0.1, format="%.2f")
    debt_pct = st.slider("Target debt share, D/(D+E) (%)", min_value=0, max_value=90,
                         value=DEFAULT_DEBT_SHARE, step=1)

    st.markdown("**Project tax rate from**")
    tax_choice = st.radio(
        "Project tax rate from", options=["United States", "Target country"], index=0,
        horizontal=True, label_visibility="collapsed", key="tax_choice",
        help="The same tax rate is applied to both the USD and the local-currency calculation.",
    )

    beta_measure_label = st.radio(
        "Beta measure", list(BETA_MEASURES), index=0,
        help="Damodaran recommends the cash-corrected unlevered beta as the pure-play business beta.",
    )

    st.header("Overrides")
    with st.expander("Unlevered beta", expanded=betas_df is None):
        if betas_df is not None:
            row = betas_df.loc[betas_df["Industry"] == industry].iloc[0]
            fetched_beta = float(row[BETA_MEASURES[beta_measure_label]])
            beta_u, beta_ovr = override_input("Unlevered beta", fetched_beta, f"beta_{industry}",
                                              fmt="%.4f", step=0.01, min_value=0.0, max_value=5.0,
                                              suffix="")
        else:
            beta_u = float(st.number_input("Unlevered beta", value=1.0, min_value=0.0,
                                           max_value=5.0, step=0.01, format="%.4f"))
            beta_ovr = True

    us_tax_fetched, us_tax_src = get_tax(US, tax_rates)
    fx_tax_fetched, fx_tax_src = get_tax(country, tax_rates)
    with st.expander("Corporate tax rates"):
        us_tax, us_tax_ovr = override_input("U.S. tax rate", us_tax_fetched, "tax_us",
                                            min_value=0.0, max_value=60.0)
        if country != US:
            fx_tax, fx_tax_ovr = override_input(f"{country} tax rate", fx_tax_fetched,
                                                f"tax_{country}", min_value=0.0, max_value=60.0)
        else:
            fx_tax, fx_tax_ovr = us_tax, us_tax_ovr

    with st.expander("10-year government bond yields"):
        with st.spinner("Fetching yields…"):
            rf_us_fetched, rf_us_meta = get_yield(US)
            rf_fx_fetched, rf_fx_meta = (get_yield(country) if country != US
                                         else (rf_us_fetched, rf_us_meta))
        rf_us_pct, rf_us_ovr = override_input("U.S. 10Y yield", rf_us_fetched, "rf_us",
                                              step=0.01, max_value=60.0)
        if country != US:
            rf_fx_pct, rf_fx_ovr = override_input(f"{country} 10Y yield", rf_fx_fetched,
                                                  f"rf_{country}", step=0.01, max_value=60.0)
        else:
            rf_fx_pct, rf_fx_ovr = rf_us_pct, rf_us_ovr

    with st.expander("Borrowing spreads"):
        s_usd_pct, s_usd_ovr = override_input("USD BBB spread", bbb_spread, "s_usd",
                                              step=0.01, max_value=30.0)
        factor_preview = (1 + rf_fx_pct / 100) / (1 + rf_us_pct / 100)
        s_fx_auto_pct = s_usd_pct * factor_preview
        s_fx_ovr = st.toggle(f"Override {ccy} BBB spread", key=f"ovr_sfx_{country}",
                             help="Off: s* = s × (1 + Rf*) / (1 + Rf)")
        if s_fx_ovr:
            s_fx_pct = float(st.number_input(f"{ccy} BBB spread (%)", value=float(round(s_fx_auto_pct, 4)),
                                             min_value=-5.0, max_value=30.0, step=0.01, format="%.4f",
                                             key=f"val_sfx_{country}"))
        else:
            s_fx_pct = None
            st.caption(f"{ccy} BBB spread (auto): **{s_fx_auto_pct:.4f}%**  \n"
                       f"= {s_usd_pct:.2f}% × (1 + {rf_fx_pct:.2f}%) / (1 + {rf_us_pct:.2f}%)")

    st.divider()
    if st.button("🔄 Refresh market data", width="stretch"):
        st.cache_data.clear()
        st.rerun()

# ---------------- Calculations ----------------
d = debt_pct / 100
erp = erp_pct / 100
tax_pct = us_tax if tax_choice == "United States" else fx_tax
tax_country = US if tax_choice == "United States" else country
tax_overridden = us_tax_ovr if tax_choice == "United States" else fx_tax_ovr
tax = tax_pct / 100
rf_usd, rf_fx, s_usd = rf_us_pct / 100, rf_fx_pct / 100, s_usd_pct / 100
s_fx_override = None if s_fx_pct is None else s_fx_pct / 100

r = cost_of_capital(beta_u, d, tax, erp, rf_usd, rf_fx, s_usd, s_fx_override)

# ---------------- Header metrics ----------------
st.subheader(f"{industry} · {country} ({ccy})")
if country == US:
    st.info("The target country is the United States, so the local-currency results equal the USD "
            "results. Pick another target country in the sidebar to compare currencies.")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Unlevered beta βᵤ", f"{beta_u:.4f}")
c2.metric("Relevered beta βₑ", f"{r['beta_e']:.4f}", help=f"D/E = {r['de']:.4f}")
c3.metric("Project tax rate τ", f"{tax_pct:.2f}%", help=f"Rate of {tax_country}")
c4.metric("Conversion factor F", f"{r['factor']:.4f}", help="(1 + Rf*) / (1 + Rf)")

# ---------------- Side-by-side results ----------------
st.markdown("### Results")
left, right = st.columns(2, gap="large")
with left:
    with st.container(border=True):
        st.markdown("#### 🇺🇸 USD")
        a, b = st.columns(2)
        a.metric("Cost of equity", pct(r["re_usd"]))
        b.metric("WACC", pct(r["wacc_usd"]))
        a.metric("Pretax cost of debt", pct(r["rd_usd"]))
        b.metric("After-tax cost of debt", pct(r["rd_usd_at"]))
        st.caption(f"Risk-free: U.S. 10Y {rf_us_pct:.2f}% · spread {s_usd_pct:.2f}%")
with right:
    with st.container(border=True):
        st.markdown(f"#### {country} · {ccy}")
        a, b = st.columns(2)
        a.metric("Cost of equity", pct(r["re_fx"]), delta=f"{(r['re_fx'] - r['re_usd']) * 100:+.2f} pp vs USD",
                 delta_color="off")
        b.metric("WACC", pct(r["wacc_fx"]), delta=f"{(r['wacc_fx'] - r['wacc_usd']) * 100:+.2f} pp vs USD",
                 delta_color="off")
        a.metric("Pretax cost of debt", pct(r["rd_fx"]))
        b.metric("After-tax cost of debt", pct(r["rd_fx_at"]))
        st.caption(f"Risk-free: {country} 10Y {rf_fx_pct:.2f}% · spread {r['s_fx'] * 100:.4f}% "
                   f"({'manual' if s_fx_override is not None else 'auto'})")

summary = pd.DataFrame({
    "Annual rate": ["Risk-free rate (10Y govt)", "Relevered equity beta", "Cost of equity",
                    "BBB borrowing spread", "Pretax cost of debt", "After-tax cost of debt", "WACC"],
    "USD": [pct(rf_usd, 4), f"{r['beta_e']:.4f}", pct(r["re_usd"], 4), pct(s_usd, 4),
            pct(r["rd_usd"], 4), pct(r["rd_usd_at"], 4), pct(r["wacc_usd"], 4)],
    ccy if ccy != "USD" else "Local (USD)": [
        pct(rf_fx, 4), f"{r['beta_e']:.4f}", pct(r["re_fx"], 4), pct(r["s_fx"], 4),
        pct(r["rd_fx"], 4), pct(r["rd_fx_at"], 4), pct(r["wacc_fx"], 4)],
})
st.dataframe(summary, hide_index=True, width="stretch")
st.caption(f"Both currencies use d = {debt_pct}%, τ = {tax_pct:.2f}% ({tax_country}) and ERP = {erp_pct:.2f}%; "
           "interest is deducted in each financing currency. Calculations use full precision; "
           "displayed rates are rounded.")

# ---------------- Step by step ----------------
with st.expander("🧮 Step-by-step calculation", expanded=False):
    st.markdown("**1. Relever the industry beta at the target financing mix**")
    st.latex(r"D/E = \frac{d}{1-d} = \frac{%.2f}{%.2f} = %.4f" % (d, 1 - d, r["de"]))
    st.latex(r"\beta_E = \beta_U\,[1 + (1-\tau)\,D/E] = %.4f \times [1 + %.4f \times %.4f] = %.4f"
             % (beta_u, 1 - tax, r["de"], r["beta_e"]))
    st.markdown("**2. USD cost of equity (CAPM, U.S. 10Y yield as risk-free proxy)**")
    st.latex(r"R_e = R_f + \beta_E \times ERP = %.4f\%% + %.4f \times %.2f\%% = %.4f\%%"
             % (rf_usd * 100, r["beta_e"], erp * 100, r["re_usd"] * 100))
    st.markdown("**3. USD cost of debt**")
    st.latex(r"R_d = R_f + s = %.4f\%% + %.4f\%% = %.4f\%%;\quad R_d(1-\tau) = %.4f\%%"
             % (rf_usd * 100, s_usd * 100, r["rd_usd"] * 100, r["rd_usd_at"] * 100))
    st.markdown("**4. USD WACC**")
    st.latex(r"WACC = (1-d)R_e + d\,R_d(1-\tau) = %.2f \times %.4f\%% + %.2f \times %.4f\%% = %.4f\%%"
             % (1 - d, r["re_usd"] * 100, d, r["rd_usd_at"] * 100, r["wacc_usd"] * 100))
    st.markdown(f"**5. Convert to {ccy} with F = (1 + Rf\\*) / (1 + Rf)**")
    st.latex(r"F = \frac{1 + %.4f\%%}{1 + %.4f\%%} = %.6f" % (rf_fx * 100, rf_usd * 100, r["factor"]))
    st.latex(r"1 + R_e^* = (1 + R_e)\,F \;\Rightarrow\; R_e^* = %.4f\%%" % (r["re_fx"] * 100))
    if s_fx_override is None:
        st.latex(r"s^* = s \times F = %.4f\%% \times %.6f = %.4f\%%"
                 % (s_usd * 100, r["factor"], r["s_fx"] * 100))
    else:
        st.latex(r"s^* = %.4f\%% \;\text{(manual override; auto would be } %.4f\%%\text{)}"
                 % (r["s_fx"] * 100, r["s_fx_auto"] * 100))
    st.latex(r"R_d^* = R_f^* + s^* = %.4f\%% + %.4f\%% = %.4f\%%;\quad R_d^*(1-\tau) = %.4f\%%"
             % (rf_fx * 100, r["s_fx"] * 100, r["rd_fx"] * 100, r["rd_fx_at"] * 100))
    st.latex(r"WACC^* = %.2f \times %.4f\%% + %.2f \times %.4f\%% = %.4f\%%"
             % (1 - d, r["re_fx"] * 100, d, r["rd_fx_at"] * 100, r["wacc_fx"] * 100))

# ---------------- Inputs & sources ----------------
st.markdown("### Inputs and sources")
inputs_df = pd.DataFrame([
    ["Industry unlevered beta", f"{beta_u:.4f}", f"Damodaran U.S. industries — {beta_measure_label}"
     + (f" ({betas_meta.get('as_of', '')})" if betas_df is not None else ""),
     status_badge(betas_meta, beta_ovr)],
    ["U.S. corporate tax rate", f"{us_tax:.2f}%", f"OECD combined statutory CIT rate — {us_tax_src}",
     status_badge(tax_meta, us_tax_ovr)],
    [f"{country} corporate tax rate", f"{fx_tax:.2f}%", f"OECD combined statutory CIT rate — {fx_tax_src}",
     status_badge(tax_meta, fx_tax_ovr)],
    ["Project tax rate used", f"{tax_pct:.2f}%", f"Rate of {tax_country} (sidebar switch)",
     status_badge(tax_meta, tax_overridden)],
    ["U.S. 10Y government yield", f"{rf_us_pct:.4f}%", rf_us_meta.get("source", "")
     + (f" ({rf_us_meta['as_of']})" if "as_of" in rf_us_meta else ""),
     status_badge(rf_us_meta, rf_us_ovr)],
    [f"{country} 10Y government yield", f"{rf_fx_pct:.4f}%", rf_fx_meta.get("source", "")
     + (f" ({rf_fx_meta['as_of']})" if "as_of" in rf_fx_meta else ""),
     status_badge(rf_fx_meta, rf_fx_ovr)],
    ["USD BBB borrowing spread", f"{s_usd_pct:.2f}%", f"Damodaran — {bbb_meta.get('as_of', '')}",
     status_badge(bbb_meta, s_usd_ovr)],
    [f"{country} ({ccy}) BBB borrowing spread", f"{r['s_fx'] * 100:.4f}%",
     "Manual input" if s_fx_override is not None else "Auto: s × (1 + Rf*) / (1 + Rf)",
     ":violet[● manual]" if s_fx_override is not None else ":blue[● derived]"],
    ["Equity risk premium", f"{erp_pct:.2f}%", "User assumption (default 6.5%)", ":gray[● assumption]"],
    ["Target debt share", f"{debt_pct}%", "User assumption (default 30%)", ":gray[● assumption]"],
], columns=["Input", "Value", "Source / choice", "Status"])
if country == US:
    inputs_df = inputs_df[~inputs_df["Input"].str.startswith(f"{US} ")].reset_index(drop=True)
st.markdown(
    "| Input | Value | Source / choice | Status |\n|---|---:|---|---|\n"
    + "\n".join("| " + " | ".join(str(v).replace("|", "/") for v in row) + " |"
                for row in inputs_df.itertuples(index=False))
)

problems = [m for m in (betas_meta, bbb_meta, tax_meta, rf_us_meta, rf_fx_meta)
            if m.get("status") in ("fallback", "failed")]
if problems:
    st.warning("Some live data could not be fetched; snapshot values are being used where shown as "
               "‘snapshot’. You can override any value in the sidebar.  \n"
               + "  \n".join(f"• {m['source']}: {m.get('error', 'unavailable')}" for m in problems))
if COUNTRIES[country][0] not in tax_rates or COUNTRIES[US][0] not in tax_rates:
    st.warning(f"{country} is not covered by the OECD corporate tax data; an indicative rate is shown. "
               "Please verify and override if needed.")

# ---------------- Assumptions ----------------
st.markdown("### Main assumptions")
st.markdown(f"""
- **Risk benchmark** – risk is measured relative to the U.S. market (CAPM with a {erp_pct:.2f}% ERP), not the
  investor's portfolio or a world portfolio. **No separate country equity risk premium** is added.
- **Project risk** – U.S. industry unlevered betas (Damodaran) proxy for project business risk; CAPM omits other
  potential sources of priced risk.
- **Interest rates** – 10-year government yields are the risk-free proxies. They embed liquidity and term premia
  and may include sovereign default risk.
- **Currency conversion** – equal real rates and relative PPP link the yield differential to expected
  depreciation, so every rate is converted with F = (1 + Rf\\*) / (1 + Rf) = {r['factor']:.4f}.
- **Credit risk** – the project borrows at a BBB spread over the government yield; by default the {ccy} spread is the
  USD spread scaled by F (it can be overridden).
- **Financing and taxes** – target leverage (d = {debt_pct}%) stays constant, interest deductions are fully and
  immediately usable (PV of tax shield = τD), and unlevering/relevering assumes debt and the tax shield have
  zero beta: βₑ = βᵤ[1 + (1 − τ)D/E]. The **same tax rate** ({tax_pct:.2f}%, {tax_country}) is used in both
  currencies.
""")

# ---------------- All-industry comparison ----------------
st.markdown("### All-industry comparison")
if betas_df is not None:
    beta_col = BETA_MEASURES[beta_measure_label]
    all_r = cost_of_capital(betas_df[beta_col], d, tax, erp, rf_usd, rf_fx, s_usd, s_fx_override)
    comp = pd.DataFrame({
        "Industry": betas_df["Industry"],
        "Firms": betas_df["Number of firms"].astype("Int64"),
        "Unlevered beta": betas_df[beta_col],
        "Relevered beta": all_r["beta_e"],
        "Cost of equity USD (%)": all_r["re_usd"] * 100,
        f"Cost of equity {ccy} (%)": all_r["re_fx"] * 100,
        "After-tax cost of debt USD (%)": r["rd_usd_at"] * 100,
        f"After-tax cost of debt {ccy} (%)": r["rd_fx_at"] * 100,
        "WACC USD (%)": all_r["wacc_usd"] * 100,
        f"WACC {ccy} (%)": all_r["wacc_fx"] * 100,
    })
    if beta_ovr:
        comp.loc[comp["Industry"] == industry, "Unlevered beta"] = beta_u
        one = cost_of_capital(beta_u, d, tax, erp, rf_usd, rf_fx, s_usd, s_fx_override)
        mask = comp["Industry"] == industry
        comp.loc[mask, "Relevered beta"] = one["beta_e"]
        comp.loc[mask, "Cost of equity USD (%)"] = one["re_usd"] * 100
        comp.loc[mask, f"Cost of equity {ccy} (%)"] = one["re_fx"] * 100
        comp.loc[mask, "WACC USD (%)"] = one["wacc_usd"] * 100
        comp.loc[mask, f"WACC {ccy} (%)"] = one["wacc_fx"] * 100

    q = st.text_input("Filter industries", placeholder="e.g. software, bank, retail")
    view = comp[comp["Industry"].str.contains(q, case=False, regex=False)] if q else comp

    def _hl(row):
        return ["background-color: rgba(255, 196, 0, 0.25)" if row["Industry"] == industry else ""] * len(row)

    num_fmt = {c: "{:.2f}" for c in comp.columns if c.endswith("(%)")}
    num_fmt.update({"Unlevered beta": "{:.4f}", "Relevered beta": "{:.4f}"})
    st.dataframe(view.style.apply(_hl, axis=1).format(num_fmt), hide_index=True,
                 width="stretch", height=420)
    st.caption(f"{len(comp)} Damodaran industry groups (incl. market totals). Selected industry highlighted. "
               f"All rows use the current settings: {country} / {ccy}, τ = {tax_pct:.2f}% ({tax_country}), "
               f"d = {debt_pct}%, ERP = {erp_pct:.2f}%. Click a column header to sort.")

    assumptions_sheet = pd.DataFrame({
        "Parameter": ["Target country", "Currency", "Project tax rate (%)", "Tax rate country",
                      "Equity risk premium (%)", "Target debt share (%)", "U.S. 10Y yield (%)",
                      f"{country} 10Y yield (%)", "USD BBB spread (%)", f"{ccy} BBB spread (%)",
                      f"{ccy} spread mode", "Conversion factor F", "Beta measure", "Generated"],
        "Value": [country, ccy, round(tax_pct, 4), tax_country, erp_pct, debt_pct, round(rf_us_pct, 4),
                  round(rf_fx_pct, 4), round(s_usd_pct, 4), round(r["s_fx"] * 100, 4),
                  "manual" if s_fx_override is not None else "auto (s × F)", round(r["factor"], 6),
                  beta_measure_label, _now()],
    })
    fname = f"cost_of_capital_{COUNTRIES[country][0]}_{datetime.now():%Y%m%d}"
    dl1, dl2, _ = st.columns([1, 1, 2])
    dl1.download_button("⬇️ Download CSV", comp.to_csv(index=False).encode("utf-8"),
                        file_name=f"{fname}.csv", mime="text/csv", width="stretch")
    dl2.download_button("⬇️ Download Excel", to_excel({"All industries": comp, "Assumptions": assumptions_sheet,
                                                      "Selected project": summary}),
                        file_name=f"{fname}.xlsx", width="stretch",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
else:
    st.info("Industry comparison unavailable because the Damodaran beta file could not be loaded.")

# ---------------- Footer ----------------
st.divider()
st.caption(
    f"Sources: [Damodaran industry betas]({BETAS_URL}) · [Damodaran ratings & spreads]({RATINGS_URL}) · "
    f"[OECD Corporate Tax Statistics]({OECD_PAGE_URL}) (via the OECD SDMX API) · "
    f"[Trading Economics bond yields]({TE_BONDS_URL}). Market data cached for 6 hours. "
    "Method: S. Rebelo, *Designing cost of capital app*. For education only — not investment advice."
)
