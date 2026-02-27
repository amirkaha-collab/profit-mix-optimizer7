import itertools
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Profit Mix Optimizer", page_icon="📊", layout="wide")

DARK_CSS = '''
<style>
html, body, [class*="css"]  { direction: rtl; text-align: right; }
section.main > div { direction: rtl; }
.block-container { padding-top: 1.2rem; padding-bottom: 2rem; max-width: 1500px; }

/* dataframe dark */
[data-testid="stDataFrame"] { direction: rtl; }
[data-testid="stDataFrame"] * { direction: rtl; }
[data-testid="stDataFrame"] div[role="grid"]{
  background: #0f1115 !important;
  color: #e8eaf0 !important;
  border-radius: 14px !important;
  border: 1px solid rgba(255,255,255,0.08) !important;
}
[data-testid="stDataFrame"] div[role="columnheader"]{
  background: #131722 !important;
  color: #e8eaf0 !important;
  border-bottom: 1px solid rgba(255,255,255,0.10) !important;
}
[data-testid="stDataFrame"] div[role="gridcell"]{
  background: #0f1115 !important;
  color: #e8eaf0 !important;
  border-bottom: 1px solid rgba(255,255,255,0.06) !important;
}

/* KPI cards */
.kpiwrap{
  display: grid;
  grid-template-columns: repeat(3, minmax(220px, 1fr));
  gap: 14px;
  margin: 0.5rem 0 1rem 0;
}
.kpicard{
  background:#0f1115;
  border:1px solid rgba(255,255,255,0.08);
  border-radius:16px;
  padding:14px 16px;
}
.kpititle{ font-size:0.9rem; opacity:0.85; margin-bottom:6px; }
.kpivalue{ font-size:1.6rem; font-weight:800; }
.kpisub{ font-size:0.85rem; opacity:0.85; margin-top:6px; line-height:1.35; }

/* Buttons */
.stButton>button { border-radius: 14px; padding: 0.6rem 1rem; font-weight: 800; }

/* Slider mobile: keep tooltip more centered by using LTR internals */
div[data-testid="stSlider"] { direction: ltr; }
div[data-testid="stSlider"] * { direction: ltr; }
div[data-testid="stSlider"] label, div[data-testid="stSlider"] p, div[data-testid="stSlider"] span {
  direction: rtl !important;
  unicode-bidi: plaintext !important;
}
</style>
'''
st.markdown(DARK_CSS, unsafe_allow_html=True)

EXCEL_DEFAULT = "קרנות_השתלמות_חשיפות.xlsx"
AUTO_EXCLUDE_SHEET_KEYWORDS = []  # ביטול סינון אוטומטי לפי שם גיליון

# Internal fixed "no-questions" constraints (effectively disabled)
MAX_ILLIQ_INTERNAL = 10_000.0
MAX_FX_INTERNAL = 10_000.0

@dataclass(frozen=True)
class Fund:
    sheet: str
    name: str
    provider: str
    abroad: float
    equity: float
    fx: float
    illiquid: float
    sharpe: float
    stdev: float

def _find_excel_in_repo() -> Optional[str]:
    try:
        files = os.listdir(".")
    except Exception:
        return None
    candidates = [fn for fn in files if fn.lower().endswith(".xlsx")]
    if EXCEL_DEFAULT in candidates:
        return EXCEL_DEFAULT
    return candidates[0] if candidates else None

def _to_float(x) -> float:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return float("nan")
    if isinstance(x, (int, float, np.integer, np.floating)):
        return float(x)
    s = str(x).strip().replace(",", "")
    if s.endswith("%"):
        s = s[:-1].strip()
    try:
        return float(s)
    except Exception:
        return float("nan")

def _pct(x: float) -> float:
    if math.isnan(x):
        return float("nan")
    return x * 100.0 if 0.0 <= x <= 1.0 else x

def _provider_from_name(fund_name: str) -> str:
    return fund_name.split(" - ", 1)[0].strip() if " - " in fund_name else fund_name.strip()

def kpi_card(title: str, badge: str, score: float, abroad: float, equity: float, fx: float, illiq: float, sharpe: float, service: float) -> str:
    return (
        '<div class="kpicard">'
        f'<div class="kpititle">{title} · {badge}</div>'
        f'<div class="kpivalue">{score:.1f}</div>'
        '<div class="kpisub">'
        f'חו״ל {abroad:.1f}% · מניות {equity:.1f}% · מט״ח {fx:.1f}% · לא־סחיר {illiq:.1f}%<br/>'
        f'שארפ {sharpe:.2f} · שירות {service:.1f}'
        '</div></div>'
    )

@st.cache_data(show_spinner=False)
def load_funds_from_excel(excel_path) -> List[Fund]:
    funds: List[Fund] = []
    xl = pd.ExcelFile(excel_path)
    for sheet in xl.sheet_names:
        sheet_str = str(sheet)

        df = pd.read_excel(xl, sheet_name=sheet_str)
        if df is None or df.empty:
            continue

        df = df.copy()

        # ניסיון התאמה: יש קבצים שבהם העמודה הראשונה היא למעשה "פרמטר" אך נקראת אחרת (למשל Unnamed: 0)
        if "פרמטר" not in df.columns and len(df.columns) > 0:
            df = df.rename(columns={df.columns[0]: "פרמטר"})

        if "פרמטר" not in df.columns:
            continue

        df["פרמטר"] = df["פרמטר"].astype(str).str.strip()

        def row(param: str):
            r = df.loc[df["פרמטר"] == param]
            return r.iloc[0] if not r.empty else None

        r_abroad = row("יעד לחו״ל")
        r_equity = row("יעד מניות")
        r_fx = row("יעד מט״ח")
        r_ill = row("מגבלת לא-סחיר")
        r_sh = row("מדד שארפ")
        r_sd = row("סטיית תקן")

        for col in df.columns:
            if col == "פרמטר":
                continue
            fund_name = str(col).strip()
            if not fund_name or fund_name.lower() == "nan":
                continue

            provider = _provider_from_name(fund_name)

            def get(r):
                if r is None:
                    return float("nan")
                return _to_float(r.get(col, float("nan")))

            abroad = _pct(get(r_abroad))
            equity = _pct(get(r_equity))
            fx = _pct(get(r_fx))
            illiquid = _pct(get(r_ill))
            sharpe = get(r_sh)
            stdev = get(r_sd)

            if any(math.isnan(v) for v in [abroad, equity, fx, illiquid]):
                continue

            funds.append(
                Fund(
                    sheet=sheet_str,
                    name=fund_name,
                    provider=provider,
                    abroad=float(abroad),
                    equity=float(equity),
                    fx=float(fx),
                    illiquid=float(illiquid),
                    sharpe=float(sharpe) if not math.isnan(sharpe) else 0.0,
                    stdev=float(stdev) if not math.isnan(stdev) else 0.0,
                )
            )
    return funds

def score_mix(values: Dict[str, float], target: Dict[str, float], weights: Dict[str, float]) -> float:
    return (
        abs(values["abroad"] - target["abroad"]) * weights["abroad"]
        + abs(values["equity"] - target["equity"]) * weights["equity"]
        + abs(values["fx"] - target["fx"]) * weights["fx"]
        + abs(values["illiquid"] - target["illiquid"]) * weights["illiquid"]
    )

def service_mix(providers: List[str], w: List[float], service_scores: Dict[str, float], default_service: float) -> float:
    return sum(ww * float(service_scores.get(p, default_service)) for p, ww in zip(providers, w))

def mix1(f1: Fund) -> Dict[str, float]:
    return {"w":[1.0], "abroad":f1.abroad, "equity":f1.equity, "fx":f1.fx, "illiquid":f1.illiquid, "sharpe":f1.sharpe}

def mix2(f1: Fund, f2: Fund, w1: float) -> Dict[str, float]:
    w1 = float(np.clip(w1, 0.0, 1.0))
    w2 = 1.0 - w1
    return {
        "w": [w1, w2],
        "abroad": w1 * f1.abroad + w2 * f2.abroad,
        "equity": w1 * f1.equity + w2 * f2.equity,
        "fx": w1 * f1.fx + w2 * f2.fx,
        "illiquid": w1 * f1.illiquid + w2 * f2.illiquid,
        "sharpe": w1 * f1.sharpe + w2 * f2.sharpe,
    }

def mix3(f1: Fund, f2: Fund, f3: Fund, w1: float, w2: float) -> Dict[str, float]:
    w1 = float(np.clip(w1, 0.0, 1.0))
    w2 = float(np.clip(w2, 0.0, 1.0))
    w3 = 1.0 - w1 - w2
    if w3 < -1e-9:
        return {}
    w3 = float(np.clip(w3, 0.0, 1.0))
    return {
        "w": [w1, w2, w3],
        "abroad": w1 * f1.abroad + w2 * f2.abroad + w3 * f3.abroad,
        "equity": w1 * f1.equity + w2 * f2.equity + w3 * f3.equity,
        "fx": w1 * f1.fx + w2 * f2.fx + w3 * f3.fx,
        "illiquid": w1 * f1.illiquid + w2 * f2.illiquid + w3 * f3.illiquid,
        "sharpe": w1 * f1.sharpe + w2 * f2.sharpe + w3 * f3.sharpe,
    }

def advantage(rank: int, dev: float, sharpe: float, service: float) -> str:
    if rank == 1:
        return f"הכי קרוב ליעד (סטייה כוללת {dev:.1f})"
    if rank == 2:
        return f"שירות+שארפ חזקים (שירות {service:.1f}, שארפ {sharpe:.2f})"
    return f"שירות משוקלל הגבוה ביותר (שירות {service:.1f})"

def compute_alternatives(
    funds: List[Fund],
    target: Dict[str, float],
    weights: Dict[str, float],
    service_scores: Dict[str, float],
    default_service: float,
    service_weight_strong: float,
    sharpe_weight: float,
    require_same_provider_only: bool,
    mix_count: int,
) -> Tuple[pd.DataFrame, str]:
    if mix_count not in (1, 2, 3):
        return pd.DataFrame(), "mix_count חייב להיות 1/2/3"
    if len(funds) < mix_count:
        return pd.DataFrame(), "אין מספיק מסלולים אחרי הסינון."

    rows = []

    if mix_count == 1:
        for f1 in funds:
            m = mix1(f1)
            dev = score_mix(m, target, weights)
            svc = service_mix([f1.provider], m["w"], service_scores, default_service)
            metric = dev - sharpe_weight * m["sharpe"] - service_weight_strong * (svc / 100.0)
            rows.append({"funds":[f1], "w":m["w"], **{k:m[k] for k in ["abroad","equity","fx","illiquid","sharpe"]},
                         "service":svc, "deviation":dev, "metric":metric})

    elif mix_count == 2:
        grid = [i / 100.0 for i in range(0, 101)]
        for f1, f2 in itertools.combinations(funds, 2):
            if require_same_provider_only and f1.provider != f2.provider:
                continue

            best = None
            for w1 in grid:
                m = mix2(f1, f2, w1)

                # internal constraints disabled (but keep safe bounds)
                if m["illiquid"] > MAX_ILLIQ_INTERNAL or m["fx"] > MAX_FX_INTERNAL:
                    continue

                dev = score_mix(m, target, weights)
                svc = service_mix([f1.provider, f2.provider], m["w"], service_scores, default_service)
                metric = dev - sharpe_weight * m["sharpe"] - service_weight_strong * (svc / 100.0)

                if best is None or metric < best["metric"]:
                    best = {"funds":[f1,f2], "w":m["w"], **{k:m[k] for k in ["abroad","equity","fx","illiquid","sharpe"]},
                            "service":svc, "deviation":dev, "metric":metric}
            if best is not None:
                rows.append(best)

    else:
        step = 0.05
        w_vals = np.arange(0.0, 1.0 + 1e-9, step).round(2).tolist()
        for f1, f2, f3 in itertools.combinations(funds, 3):
            providers = [f1.provider, f2.provider, f3.provider]
            if require_same_provider_only and not (providers[0] == providers[1] == providers[2]):
                continue

            best = None
            for w1 in w_vals:
                for w2 in w_vals:
                    if w1 + w2 > 1.0 + 1e-9:
                        continue
                    m = mix3(f1, f2, f3, w1, w2)
                    if not m:
                        continue

                    if m["illiquid"] > MAX_ILLIQ_INTERNAL or m["fx"] > MAX_FX_INTERNAL:
                        continue

                    dev = score_mix(m, target, weights)
                    svc = service_mix(providers, m["w"], service_scores, default_service)
                    metric = dev - sharpe_weight * m["sharpe"] - service_weight_strong * (svc / 100.0)

                    if best is None or metric < best["metric"]:
                        best = {"funds":[f1,f2,f3], "w":m["w"], **{k:m[k] for k in ["abroad","equity","fx","illiquid","sharpe"]},
                                "service":svc, "deviation":dev, "metric":metric}
            if best is not None:
                rows.append(best)

    if not rows:
        return pd.DataFrame(), "לא נמצאו פתרונות."

    cand = pd.DataFrame(rows).sort_values("metric", ascending=True).reset_index(drop=True)

    chosen = []
    used_provider_sets = []
    used_funds = set()

    for _, r in cand.iterrows():
        funds_list: List[Fund] = r["funds"]
        providers_set = tuple(sorted({f.provider for f in funds_list}))
        funds_names_set = tuple(sorted({f.name for f in funds_list}))

        # avoid repeating exact funds across alternatives
        if any(fn in used_funds for fn in funds_names_set):
            continue

        # if not forcing same-provider-only, try to diversify providers between alternatives
        if not require_same_provider_only:
            if any(set(prev) & set(providers_set) for prev in used_provider_sets):
                continue

        chosen.append(r)
        used_provider_sets.append(providers_set)
        for fn in funds_names_set:
            used_funds.add(fn)

        if len(chosen) == 3:
            break

    if not chosen:
        chosen = [cand.iloc[i] for i in range(min(3, len(cand)))]

    out_rows = []
    for i, r in enumerate(chosen, start=1):
        funds_list: List[Fund] = r["funds"]
        w_list: List[float] = r["w"]

        row = {
            "חלופה": f"חלופה #{i}",
            "סטייה מהיעד (Score)": float(r["deviation"]),
            "חו״ל (%)": float(r["abroad"]),
            "מניות (%)": float(r["equity"]),
            "מט״ח (%)": float(r["fx"]),
            "לא־סחיר (%)": float(r["illiquid"]),
            "שארפ משוקלל": float(r["sharpe"]),
            "ציון שירות": float(r["service"]),
            "יתרון": advantage(i, float(r["deviation"]), float(r["sharpe"]), float(r["service"])),
        }

        for j, (f, w) in enumerate(zip(funds_list, w_list), start=1):
            row[f"מסלול #{j}"] = f.name
            row[f"משקל #{j}"] = f"{w*100:.1f}%"
            row[f"מנהל #{j}"] = f.provider
            row[f"גיליון #{j}"] = f.sheet

        # ensure columns exist even if fewer tracks
        for jj in range(len(funds_list)+1, 4):
            row[f"מסלול #{jj}"] = ""
            row[f"משקל #{jj}"] = ""
            row[f"מנהל #{jj}"] = ""
            row[f"גיליון #{jj}"] = ""

        out_rows.append(row)

    return pd.DataFrame(out_rows), ""


st.title("Profit Mix Optimizer")
st.caption("מחשב 3 חלופות לשילוב בין 1/2/3 מסלולי השקעה, עם דגש חזק על איכות שירות + עמידה ביעדים.")

with st.sidebar:
    st.markdown("### הגדרות")
    if st.button("איפוס הגדרות", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()
    st.divider()
    uploaded = st.file_uploader("העלה Excel (אופציונלי)", type=["xlsx"])
    st.caption("אם לא תעלה — האפליקציה משתמשת בקובץ שבאותה תיקייה בריפו.")

excel_path = uploaded if uploaded is not None else _find_excel_in_repo()
if excel_path is None:
    st.error("לא נמצא קובץ Excel. העלה ‎.xlsx‎ או הוסף אותו לריפו.")
    st.stop()

tabs = st.tabs(["הגדרות יעד", "תוצאות (3 חלופות)", "פירוט / שקיפות"])

with tabs[0]:
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        target_abroad = st.slider("יעד חו״ל (%)", 0, 100, 60)
    with c2:
        target_equity = st.slider("יעד מניות (%)", 0, 100, 40)
    with c3:
        target_fx = st.slider("יעד מט״ח (%)", 0, 100, 50)
    with c4:
        target_ill = st.slider("יעד לא־סחיר (%)", 0, 40, 10)

    st.markdown("#### מספר מסלולים בשילוב")
    mix_count = st.radio("שילוב של", options=[1, 2, 3], horizontal=True, format_func=lambda x: f"{x} מסלולים")

    st.markdown("#### משקולות ליעדים (כמה חשוב להתקרב)")
    w1, w2, w3, w4 = st.columns(4)
    with w1:
        w_abroad = st.slider("חשיבות חו״ל", 0.0, 3.0, 1.0, 0.1)
    with w2:
        w_equity = st.slider("חשיבות מניות", 0.0, 3.0, 1.0, 0.1)
    with w3:
        w_fx = st.slider("חשיבות מט״ח", 0.0, 3.0, 1.0, 0.1)
    with w4:
        w_ill = st.slider("חשיבות לא־סחיר", 0.0, 3.0, 1.0, 0.1)

    st.markdown("#### שארפ + שירות (שירות תמיד מקבל משקל גדול)")
    s1, s2 = st.columns(2)
    with s1:
        sharpe_weight = st.slider("משקל לשארפ", 0.0, 5.0, 1.0, 0.1)
    with s2:
        service_weight_strong = st.slider("משקל לשירות", 0.0, 8.0, 4.0, 0.1)

    st.markdown("#### אפשרויות נוספות")
    o1, o2 = st.columns(2)
    with o1:
        require_same_provider_only = st.toggle("חייב שילובים רק מאותו גוף מנהל", value=False)
    with o2:
        default_service = st.slider("ברירת מחדל לציון שירות (אם לא הוגדר)", 0, 100, 70)

    st.markdown("#### ציוני שירות")
    svc_csv = st.file_uploader("CSV ציוני שירות (provider, score)", type=["csv"])
    service_scores: Dict[str, float] = {}
    if svc_csv is not None:
        try:
            svc_df = pd.read_csv(svc_csv)
            for _, rr in svc_df.iterrows():
                p = str(rr.get("provider", "")).strip()
                sc = _to_float(rr.get("score", float("nan")))
                if p and not math.isnan(sc):
                    service_scores[p] = float(sc)
            st.success(f"נטענו ציוני שירות ל-{len(service_scores)} מנהלים.")
        except Exception as e:
            st.error(f"לא הצלחתי לקרוא CSV: {e}")

    st.info("גיליונות IRA/ניהול אישי/סלייס מדולגים אוטומטית, וגם גיליונות ריקים/בלי 'פרמטר'.")

    st.session_state["TARGET"] = {"abroad": float(target_abroad), "equity": float(target_equity), "fx": float(target_fx), "illiquid": float(target_ill)}
    st.session_state["WEIGHTS"] = {"abroad": float(w_abroad), "equity": float(w_equity), "fx": float(w_fx), "illiquid": float(w_ill)}
    st.session_state["sharpe_weight"] = float(sharpe_weight)
    st.session_state["service_weight_strong"] = float(service_weight_strong)
    st.session_state["require_same_provider_only"] = bool(require_same_provider_only)
    st.session_state["mix_count"] = int(mix_count)
    st.session_state["service_scores"] = service_scores
    st.session_state["default_service"] = float(default_service)

    # ✅ explicit calculate button (was missing)
    if st.button("חשב", use_container_width=True, type="primary"):
        st.session_state["RUN_CALC"] = True
        st.session_state["LAST_RUN_ID"] = st.session_state.get("LAST_RUN_ID", 0) + 1
        st.success("עודכן ✔️ עבור לטאב 'תוצאות'")

    if "RUN_CALC" not in st.session_state:
        st.session_state["RUN_CALC"] = True  # first load auto

with tabs[1]:
    st.subheader("תוצאות (3 חלופות)")

    if not st.session_state.get("RUN_CALC", False):
        st.info("לחץ על 'חשב' בטאב 'הגדרות יעד' כדי להציג תוצאות.")
        st.stop()

    funds = load_funds_from_excel(excel_path)
    if len(funds) < 1:
        st.warning("אין מסלולים תקינים בקובץ אחרי סינון אוטומטי.")
        st.stop()

    out, err = compute_alternatives(
        funds=funds,
        target=st.session_state["TARGET"],
        weights=st.session_state["WEIGHTS"],
        service_scores=st.session_state.get("service_scores", {}),
        default_service=st.session_state.get("default_service", 70.0),
        service_weight_strong=st.session_state["service_weight_strong"],
        sharpe_weight=st.session_state["sharpe_weight"],
        require_same_provider_only=st.session_state["require_same_provider_only"],
        mix_count=st.session_state["mix_count"],
    )

    if err:
        st.warning(err)
        st.stop()

    cards = []
    for i in range(min(3, len(out))):
        r = out.iloc[i]
        badge = "קרוב ביותר" if i == 0 else ("חלופה חזקה" if i == 1 else "חלופת שירות")
        cards.append(kpi_card(
            title=r["חלופה"],
            badge=badge,
            score=float(r["סטייה מהיעד (Score)"]),
            abroad=float(r["חו״ל (%)"]),
            equity=float(r["מניות (%)"]),
            fx=float(r["מט״ח (%)"]),
            illiq=float(r["לא־סחיר (%)"]),
            sharpe=float(r["שארפ משוקלל"]),
            service=float(r["ציון שירות"]),
        ))
    st.markdown('<div class="kpiwrap">' + "".join(cards) + "</div>", unsafe_allow_html=True)

    df = out.copy()
    for col in ["חו״ל (%)", "מניות (%)", "מט״ח (%)", "לא־סחיר (%)"]:
        df[col] = df[col].map(lambda x: f"{float(x):.1f}%")
    df["שארפ משוקלל"] = df["שארפ משוקלל"].map(lambda x: f"{float(x):.2f}")
    df["ציון שירות"] = df["ציון שירות"].map(lambda x: f"{float(x):.1f}")
    df["סטייה מהיעד (Score)"] = df["סטייה מהיעד (Score)"].map(lambda x: f"{float(x):.1f}")

    column_config = {
        "מסלול #1": st.column_config.TextColumn(width="large"),
        "מסלול #2": st.column_config.TextColumn(width="large"),
        "מסלול #3": st.column_config.TextColumn(width="large"),
        "יתרון": st.column_config.TextColumn(width="large"),
    }
    st.dataframe(df, use_container_width=True, hide_index=True, column_config=column_config)

with tabs[2]:
    st.subheader("פירוט / שקיפות")
    st.write("• סינון אוטומטי: גיליונות IRA/ניהול אישי/סלייס/‏slice מדולגים, וגם גיליונות ריקים/בלי 'פרמטר'.")
    st.write("• מצב חישוב: יציב/יסודי.")
    st.write("• 1 מסלול: דירוג לפי Score + שארפ + שירות.")
    st.write("• 2 מסלולים: משקלים 0..100 בצעד 1%.")
    st.write("• 3 מסלולים: גריד simplex בצעד 5% (זמן ריצה סביר).")
    with st.expander("היעדים שהוגדרו"):
        st.write(st.session_state.get("TARGET", {}))
    with st.expander("המשקולות שהוגדרו"):
        st.write(st.session_state.get("WEIGHTS", {}))
