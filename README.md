# 🌍 Foreign Cost of Capital

A Streamlit app that compares a project's **cost of equity**, **cost of debt** and **WACC** in **USD** and in the
**currency of a selected country**. It follows the method in S. Rebelo, *Designing cost of capital app*.

## What it does

- Pick an **industry** (Damodaran U.S. industry groups) and a **target country** (54 countries with Trading
  Economics 10-year yields).
- Adjust the **equity risk premium** and the **target debt share**.
- Switch the **project tax rate** between the **United States** and the **target country**. The same rate is
  used in both currency calculations.
- **Override** any fetched input: unlevered beta, U.S. and foreign corporate tax rates, USD BBB spread, and
  U.S. and foreign 10-year yields.
- **Foreign BBB spread**: auto-calculated as `s* = s × (1 + Rf*) / (1 + Rf)`, or set by hand with a toggle. When
  the toggle is off, the app shows the auto value.
- Shows results **side by side** (USD vs. local currency), a step-by-step calculation, an inputs-and-sources
  table with live, snapshot or manual status, and the main assumptions.
- **All-industry comparison** using the current settings, downloadable as **CSV** or **Excel** (the Excel file
  includes the assumptions and the selected-project summary).

**Defaults on first load:** Software (System & Application) · United States · U.S. tax rate · ERP 6.5% ·
debt share 30%.

## Method

| Step | Formula |
|---|---|
| Debt-to-equity | `D/E = d / (1 − d)` |
| Relever beta | `βE = βU × [1 + (1 − τ) × D/E]` |
| USD cost of equity (CAPM) | `Re = Rf + βE × ERP` |
| USD pretax cost of debt | `Rd = Rf + s` |
| Conversion factor | `F = (1 + Rf*) / (1 + Rf)` |
| Local cost of equity | `1 + Re* = (1 + Re) × F` |
| Local BBB spread (default) | `s* = s × F` |
| Local pretax cost of debt | `Rd* = Rf* + s*` |
| WACC (each currency) | `WACC = (1 − d) Re + d Rd (1 − τ)` |

`Rf` is the U.S. 10-year yield and `Rf*` is the target country's 10-year yield. Check against the Colombia
example (βU 0.80, d 30%, τ 35%, ERP 6.5%, Rf 4%, Rf* 12%, s 1.5%): USD WACC **8.5265%**, COP WACC **16.0670%**.

### Key assumptions
Risk is measured relative to the U.S. market, with no separate country equity premium. U.S. industry betas proxy
for project risk. Government yields serve as risk-free proxies. Equal real rates and relative PPP link yields to
expected depreciation. Target leverage stays constant, interest deductions are fully usable, and debt and the tax
shield have zero beta.

## Data sources

| Input | Source | How it is fetched |
|---|---|---|
| Industry unlevered betas | [Damodaran `betas.xls`](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | Excel download (`xlrd`). Choose "corrected for cash" (default) or plain unlevered beta. |
| BBB borrowing spread | [Damodaran ratings](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | HTML parse, Baa2/BBB row for large non-financial firms |
| Corporate tax rates | [OECD Corporate Tax Statistics](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html) | The OECD page is rendered in the browser behind a bot challenge, so the app reads the same dataset from the official **OECD SDMX API** (`DF_CIT`, combined central + sub-central statutory rate, latest year). |
| 10-year government yields | [Trading Economics](https://tradingeconomics.com/bonds) | Scrapes the `/bonds` table. If Trading Economics blocks that table (it is loaded by JavaScript behind a challenge), the app scrapes the country's own page `/<country>/government-bond-yield`. |

Live data is cached for 6 hours. Use **🔄 Refresh market data** in the sidebar to reload it. If a source can't be
reached, the app uses a dated snapshot, marks it **snapshot** in the inputs table, and you can override the value.
Russia and Taiwan are not in the OECD tax dataset; their rates are indicative and flagged.

## Run locally (Python 3.12)

```bash
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Community Cloud

1. Push `app.py`, `requirements.txt` and `README.md` to a GitHub repository.
2. Go to [share.streamlit.io](https://share.streamlit.io) and choose **Create app**, then pick the repo and branch,
   with `app.py` as the main file.
3. Under **Advanced settings**, choose **Python 3.12**, then deploy.

## Disclaimer
For educational purposes only. Market inputs change daily and scraped sources may change format. This is not
investment advice.
