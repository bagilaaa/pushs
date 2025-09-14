import io
import os
import re
import zipfile
import datetime as dt
import pandas as pd
import numpy as np
import streamlit as st

# ================== PAGE & THEME ==================
st.set_page_config(page_title="Персонализация пуш-уведомлений", page_icon="📲", layout="wide")

# Корпоративные оттенки
CORP_GREEN = "#2DBE60"
CORP_GOLD  = "#C8A86B"
DARK_BG    = "#0F1115"
CARD_BG    = "#161A22"
TEXT       = "#E6E6EB"
MUTED      = "#9AA0A6"

CSS = f"""
<style>
:root {{
  --green: {CORP_GREEN};
  --gold:  {CORP_GOLD};
  --bg:    {DARK_BG};
  --card:  {CARD_BG};
  --text:  {TEXT};
  --muted: {MUTED};
}}
.block-container {{ padding-top: 3.6rem !important; }}
html, body, [data-testid="stAppViewContainer"] {{
  background:
    radial-gradient(1100px 550px at 8% 8%, rgba(45,190,96,.15), transparent 40%),
    radial-gradient(900px 450px at 95% 15%, rgba(200,168,107,.12), transparent 40%),
    var(--bg);
  color: var(--text);
}}
h1, h2, h3, h4, h5 {{ color: var(--text); }}
.sidebar .sidebar-content, [data-testid="stSidebar"] > div:first-child {{ background: var(--card); }}
div.stButton > button {{
  background: linear-gradient(90deg, var(--green), #00d4ff);
  color: white; border: 0; border-radius: 14px; padding: .6rem 1rem; font-weight: 700;
}}
div.stDownloadButton > button {{
  background: linear-gradient(90deg, #00c853, #64dd17);
  color: #081208; border: 0; border-radius: 14px; padding: .6rem 1rem; font-weight: 700;
}}
[data-testid="stFileUploader"] {{
  background: var(--card); border-radius: 12px; border: 1px solid rgba(255,255,255,.08);
}}
.card {{ background: var(--card); border: 1px solid rgba(255,255,255,.06); border-radius: 16px; padding: 14px 16px; }}
.muted {{ color: var(--muted); font-size:.9rem; }}
table td, table th {{ color: var(--text); }}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

st.markdown("## 📲 Персонализация пуш-уведомлений")

# ================== Каталог продуктов ==================
P_TRAVEL = "Карта для путешествий"
P_PREM   = "Премиальная карта"
P_CC     = "Кредитная карта"
P_FX     = "Обмен валют"
P_CASH   = "Кредит наличными"
P_DEP_MULTI = "Депозит Мультивалютный (KZT/USD/RUB/EUR)"
P_DEP_SAVE  = "Депозит Сберегательный (защита KDIF)"
P_DEP_ACC   = "Депозит Накопительный"
P_INV    = "Инвестиции"
P_GOLD   = "Золотые слитки"
ALL_PRODUCTS = [P_TRAVEL,P_PREM,P_CC,P_FX,P_CASH,P_DEP_MULTI,P_DEP_SAVE,P_DEP_ACC,P_INV,P_GOLD]

# ================== Утилиты ==================
def month_name_ru_prep(ts):
    m = ['январе','феврале','марте','апреле','мае','июне','июле','августе','сентябре','октябре','ноябре','декабре']
    return m[ts.month-1]

def fmt_int_kzt(a):
    try: return f"{int(round(float(a))):,}".replace(",", " ") + " ₸"
    except Exception: return "0 ₸"

def clamp_push(txt:str, max_len=220) -> str:
    txt = re.sub(r"\s+", " ", str(txt)).strip()
    txt = re.sub(r"!{2,}", "!", txt)
    return txt[:max_len].rstrip()

def tweak_for_age(text:str, age:int) -> str:
    if age is None or pd.isna(age): return text
    if age < 50:
        text = text.replace("Оформить сейчас.", "Оформить сейчас — и кешбэк начнёт работать на вас.")
        text = text.replace("Открыть карту.", "Открыть карту — и часть расходов вернётся заметнее.")
        text = text.replace("Оформить карту.", "Оформить карту — быстро и без лишних шагов.")
    else:
        text = text.replace("начнёт работать на вас", "будет начисляться стабильно")
        text = text.replace("заметнее", "больше")
    return text

# ================== Чтение датасета ==================
def read_clients(zf):
    return pd.read_csv(zf.open('case 1/clients.csv'))

def read_client_frames(zf, code:int):
    tx_cols = ['client_code','name','product','status','city','date','category','amount','currency']
    tr_cols = ['client_code','name','product','status','city','date','type','direction','amount','currency']
    try:
        tx = pd.read_csv(zf.open(f'case 1/client_{code}_transactions_3m.csv'), parse_dates=['date'])
    except KeyError:
        tx = pd.DataFrame(columns=tx_cols)
    try:
        tr = pd.read_csv(zf.open(f'case 1/client_{code}_transfers_3m.csv'), parse_dates=['date'])
    except KeyError:
        tr = pd.DataFrame(columns=tr_cols)
    return tx, tr

# ================== Выгода (KZT/мес) ==================
def expected_benefits(profile, tx, tr):
    out = {p: 0.0 for p in ALL_PRODUCTS}
    months = 3
    if len(tx)==0 and len(tr)==0: return out
    cat_month = tx.groupby('category')['amount'].sum() / months if len(tx) else pd.Series(dtype=float)
    total_month = tx['amount'].sum() / months if len(tx) else 0.0
    avg_bal = float(profile.get('avg_monthly_balance_KZT', 0) or 0)

    travel_month = sum(cat_month.get(c,0) for c in ['Путешествия','Такси','Отели'])
    out[P_TRAVEL] = 0.04 * travel_month

    prem_rate = 0.02
    if 1_000_000 <= avg_bal < 6_000_000: prem_rate = 0.03
    if avg_bal >= 6_000_000: prem_rate = 0.04
    rest = cat_month.get('Кафе и рестораны',0)
    jewel = cat_month.get('Ювелирные украшения',0)
    perfume = cat_month.get('Косметика и Парфюмерия',0)
    base_spend = max(total_month - (rest+jewel+perfume), 0)
    prem_b = prem_rate * base_spend + 0.04*(rest+jewel+perfume)
    out[P_PREM] = min(prem_b, 100_000)

    fav3 = cat_month.sort_values(ascending=False).head(3).sum() if len(cat_month) else 0
    online = sum(cat_month.get(c,0) for c in ['Смотрим дома','Играем дома','Едим дома','Кино','Развлечения'])
    out[P_CC] = 0.10 * fav3 + 0.10 * online

    fx_vol_m = float(tr[tr['type'].isin(['fx_buy','fx_sell'])]['amount'].abs().sum())/months if len(tr) else 0.0
    out[P_FX] = 0.003 * fx_vol_m

    out[P_DEP_SAVE]  = 0.165 * avg_bal / 12
    out[P_DEP_ACC]   = 0.155 * avg_bal / 12
    out[P_DEP_MULTI] = 0.145 * avg_bal / 12

    return out

# ================== Сигналы поведения ==================
def make_signals(profile, tx, tr):
    sig = {}
    sig["client_code"] = int(profile.get("client_code"))
    sig["name"] = profile.get("name", "Клиент")
    sig["age"] = int(profile.get("age", 0) or 0)
    sig["status"] = profile.get("status", "")
    sig["city"] = profile.get("city", "")
    sig["avg_balance"] = float(profile.get("avg_monthly_balance_KZT", 0) or 0)
    def sum_cat(cats):
        if len(tx)==0: return 0.0
        return float(tx[tx['category'].isin(cats)]['amount'].sum())
    sig["total_spend_3m"] = float(tx['amount'].sum()) if len(tx) else 0.0
    sig["travel_spend_3m"] = sum_cat(['Путешествия','Такси','Отели'])
    sig["restaurants_3m"] = sum_cat(['Кафе и рестораны'])
    sig["online_3m"] = sum_cat(['Смотрим дома','Играем дома','Едим дома','Кино','Развлечения'])
    sig["taxi_count"] = int((tx['category']=='Такси').sum()) if len(tx) else 0
    if len(tx):
        top3 = tx.groupby('category')['amount'].sum().sort_values(ascending=False).head(3)
        sig["top_categories"] = list(top3.index)
    else:
        sig["top_categories"] = []
    fx_vol = float(tr[tr['type'].isin(['fx_buy','fx_sell'])]['amount'].abs().sum()) if len(tr) else 0.0
    sig["fx_turnover_3m"] = fx_vol
    sig["has_salary"] = bool(len(tr) and (tr['type']=='salary_in').any())
    return sig

# ================== Скоринг + базовый выбор продукта ==================
def pick_best_and_push(profile, tx, tr):
    name    = profile.get('name', 'Клиент')
    avg_bal = profile.get('avg_monthly_balance_KZT', 0)
    age     = profile.get('age', None)

    months = 3
    total_spend = float(tx['amount'].sum()) if len(tx) else 0.0
    cat_sum = tx.groupby('category')['amount'].sum().sort_values(ascending=False) if len(tx) else pd.Series(dtype=float)
    fav_cats = list(cat_sum.head(3).index) if len(cat_sum) else []
    def sum_cat(cats): return float(tx[tx['category'].isin(cats)]['amount'].sum()) if len(tx) else 0.0

    travel_spend  = sum_cat(['Путешествия','Отели','Такси'])
    taxi_cnt      = int((tx['category']=='Такси').sum()) if len(tx) else 0
    hotels_cnt    = int((tx['category']=='Отели').sum()) if len(tx) else 0
    rest_spend    = sum_cat(['Кафе и рестораны'])
    jew_perf      = sum_cat(['Ювелирные украшения','Косметика и Парфюмерия'])
    online_spend  = sum_cat(['Смотрим дома','Играем дома','Едим дома','Кино','Развлечения'])

    fx_vol     = float(tr[tr['type'].isin(['fx_buy','fx_sell'])]['amount'].abs().sum()) if len(tr) else 0.0
    atm_vol    = float(tr[tr['type']=='atm_withdrawal']['amount'].abs().sum()) if len(tr) else 0.0
    card_out   = float(tr[tr['type']=='card_out']['amount'].abs().sum()) if len(tr) else 0.0
    kz_transfs = atm_vol + card_out
    invest_vol = float(tr[tr['type'].isin(['invest_out','invest_in'])]['amount'].abs().sum()) if len(tr) else 0.0
    gold_vol   = float(tr[tr['type'].isin(['gold_buy_out','gold_sell_in'])]['amount'].abs().sum()) if len(tr) else 0.0
    salary_in  = float(tr[tr['type']=='salary_in']['amount'].sum()) if len(tr) else 0.0
    stipend_in = float(tr[tr['type']=='stipend_in']['amount'].sum()) if len(tr) else 0.0

    top3_spend = float(cat_sum.head(3).sum()) if len(cat_sum) else 0.0

    scores = {p:0.0 for p in ALL_PRODUCTS}
    scores[P_TRAVEL] = (travel_spend / max(1, total_spend)) * 100 + taxi_cnt*0.1 + hotels_cnt*0.2
    scores[P_PREM]   = (avg_bal>=1_000_000)*10 + (avg_bal>=6_000_000)*10 + (kz_transfs/max(1,total_spend))*10 + (rest_spend>0)*3 + (jew_perf>0)*5
    scores[P_CC]     = (top3_spend/max(1,total_spend))*30 + (online_spend>0)*5 + (salary_in>0 or stipend_in>0)*2
    scores[P_FX]     = (fx_vol>0)*15 + (fx_vol/(abs(tr['amount']).sum() or 1))*10 if len(tr) else 0
    free_cash_signal = (avg_bal>200_000) * (total_spend/months < avg_bal*0.5)
    scores[P_DEP_SAVE]  = (avg_bal>500_000)*5 + (free_cash_signal)*5
    scores[P_DEP_ACC]   = (avg_bal>200_000)*4 + (salary_in>0)*2
    scores[P_DEP_MULTI] = (avg_bal>300_000)*2
    scores[P_INV]    = (invest_vol>0)*10 + (avg_bal<800_000)*3 + (salary_in>0)*2
    scores[P_GOLD]   = (gold_vol>0)*15 + (avg_bal>1_000_000)*2
    scores[P_CASH]   = 0

    benefits = expected_benefits(profile, tx, tr)
    for p in benefits: scores[p] += np.log1p(max(0.0, benefits[p]))

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best = ranked[0][0]

    last_month = (tx['date'].max().to_pydatetime().date().replace(day=1)
                  if len(tx) else dt.date(2025,8,1))
    m_prep = month_name_ru_prep(pd.Timestamp(last_month))

    # базовый фоллбек-текст (на случай отсутствия вариативного генератора)
    if best == P_TRAVEL:
        gain = benefits[P_TRAVEL]
        push = f"{name}, в {m_prep} у вас много поездок и такси. С картой для путешествий часть расходов вернулась бы кешбэком ≈{fmt_int_kzt(gain)} в месяц. Открыть карту."
    elif best == P_PREM:
        gain = benefits[P_PREM]
        push = f"{name}, у вас стабильно высокий остаток и активные операции. Премиальная карта вернёт до 4% и снимет комиссии; потенциал ≈{fmt_int_kzt(gain)} в месяц. Оформить сейчас."
    elif best == P_CC:
        gain = benefits[P_CC]
        cats = ", ".join(fav_cats[:3]) if fav_cats else "ваших любимых категориях"
        push = f"{name}, ваши топ-категории — {cats}. Кредитная карта даёт до 10% и онлайн-кешбэк; ориентировочно ≈{fmt_int_kzt(gain)} в месяц. Оформить карту."
    elif best == P_FX:
        gain = benefits[P_FX]
        push = f"{name}, вы часто меняете валюту. В приложении — выгодный курс и авто-покупка по целевому; экономия ≈{fmt_int_kzt(gain)} в месяц. Настроить обмен."
    elif best == P_DEP_SAVE:
        gain = benefits[P_DEP_SAVE]
        push = f"{name}, свободные средства могут работать. Сберегательный вклад 16,50% — потенциально ≈{fmt_int_kzt(gain)} в месяц при текущем остатке. Открыть вклад."
    elif best == P_DEP_ACC:
        gain = benefits[P_DEP_ACC]
        push = f"{name}, удобно копить без снятия до цели. Накопительный вклад 15,50% ориентировочно даст ≈{fmt_int_kzt(gain)} в месяц. Открыть вклад."
    elif best == P_DEP_MULTI:
        gain = benefits[P_DEP_MULTI]
        push = f"{name}, держите часть средств в валютах. Мультивалютный вклад 14,50% — ≈{fmt_int_kzt(gain)} в месяц с доступом к средствам. Открыть вклад."
    elif best == P_INV:
        push = f"{name}, попробуйте инвестиции: без комиссий на старт и порог от 6 ₸. Начните с небольшой суммы и пополняйте по плану. Открыть брокерский счёт."
    elif best == P_GOLD:
        push = f"{name}, для долгосрочного сбережения — золотые слитки 999,9: покупка в отделении, предзаказ в приложении. Узнать подробнее."
    else:
        push = f"{name}, у нас есть продукты, которые помогут получать больше выгоды от повседневных расходов. Посмотреть подборку."

    signals = make_signals(profile, tx, tr)
    signals["month_prep"] = m_prep
    signals["best_product"] = best
    signals["benefit_best"] = benefits.get(best, 0.0)

    # ==== ВАРИАТИВНЫЙ ПУШ (seed = client_code) ====
    push = generate_push_variant(best, benefits, signals)

    # возрастные правки + длина
    push = clamp_push(tweak_for_age(push, signals["age"]))

    return best, push, ranked, benefits, signals

# ================== Вариативный локальный пуш (без ИИ) ==================
def generate_push_variant(best_product:str, benefits:dict, s:dict) -> str:
    rng = np.random.RandomState(int(s.get("client_code", 0)) % (2**32))
    name = s.get("name", "Клиент")
    city = s.get("city") or ""
    month = s.get("month_prep") or ""
    gain = fmt_int_kzt(benefits.get(best_product, 0))
    cats = s.get("top_categories", []) or []
    taxi = s.get("taxi_count", 0)
    online = int(round(s.get("online_3m", 0)))
    travel = int(round(s.get("travel_spend_3m", 0)))
    avg_bal = int(round(s.get("avg_balance", 0)))
    has_salary = s.get("has_salary", False)

    hooks = [
        f"{name}, заметили ваш стиль расходов",
        f"{name}, по вашим операциям в {month}",
        f"{name}, посмотрели недавние траты",
        f"{name}, у вас интересный профиль",
    ]
    if city: hooks += [f"{name}, в {city} это особенно актуально", f"{name}, учитывая операции в {city}"]

    verbs = ["вернул бы кешбэком", "сэкономил бы", "дал бы выгоды", "мог бы приносить выгоду"]
    online_hint = "" if online == 0 else f" и онлайн-платежи ≈{fmt_int_kzt(online)} за 3 мес."
    taxi_hint = "" if taxi == 0 else f" Часто такси ({taxi} поезд.)."
    travel_hint = "" if travel == 0 else f" Путешествия на сумму ≈{fmt_int_kzt(travel)} за 3 мес."

    ctas = {
        P_TRAVEL: ["Открыть карту.", "Подключить карту.", "Начать пользоваться."],
        P_PREM: ["Оформить сейчас.", "Подключить премиум.", "Получить привилегии."],
        P_CC: ["Оформить карту.", "Получить карту.", "Подать заявку."],
        P_FX: ["Настроить обмен.", "Выбрать авто-покупку.", "Открыть курс-цель."],
        P_DEP_SAVE: ["Открыть вклад.", "Начать копить.", "Перевести остаток."],
        P_DEP_ACC: ["Открыть вклад.", "Начать копить к цели.", "Подключить автопополнение."],
        P_DEP_MULTI: ["Открыть вклад.", "Разложить по валютам.", "Посмотреть ставки."],
        P_INV: ["Открыть брокерский счёт.", "Начать инвестировать.", "Попробовать с мини-суммы."],
        P_GOLD: ["Посмотреть детали.", "Оформить предзаказ.", "Узнать условия."],
        P_CASH: ["Узнать лимит.", "Оформить заявку.", "Проверить предложения."]
    }

    hook = rng.choice(hooks)
    cta = rng.choice(ctas.get(best_product, ["Посмотреть детали."]))
    verb = rng.choice(verbs)

    # разные шаблоны под продукт
    if best_product == P_TRAVEL:
        body = f"в {month} у вас много поездок.{taxi_hint}{travel_hint} Карта для путешествий {verb} ≈{gain} в мес."
    elif best_product == P_PREM:
        tier = "до 4%" if avg_bal >= 1_000_000 else "2–3%"
        body = f"высокий средний остаток и активные операции. Премиальная карта даст {tier} кешбэка и снизит комиссии — потенциал ≈{gain} в мес."
    elif best_product == P_CC:
        cats_str = ", ".join(cats[:3]) if cats else "ваших любимых категориях"
        body = f"ваши топ-категории — {cats_str}.{online_hint} Кредитная карта до 10% кешбэка — ориентир ≈{gain} в мес."
    elif best_product == P_FX:
        body = f"вы часто меняете валюту. Автопокупка по целевому и выгодные курсы — экономия ≈{gain} в мес."
    elif best_product == P_DEP_SAVE:
        body = f"свободные средства могут работать. Сберегательный вклад 16,50% — ≈{gain} в мес. при текущем остатке."
    elif best_product == P_DEP_ACC:
        extra = " с зарплатой — автоматически" if has_salary else ""
        body = f"удобно копить к цели{extra}. Накопительный вклад 15,50% даст ≈{gain} в мес."
    elif best_product == P_DEP_MULTI:
        body = f"часть средств лучше держать в валютах. Мультивалютный вклад 14,50% — ≈{gain} в мес. и быстрый доступ."
    elif best_product == P_INV:
        body = "попробуйте инвестиции: без комиссий на старт, порог от 6 ₸. Начните с небольшой суммы по плану."
    elif best_product == P_GOLD:
        body = "для долгого сбережения — слитки 999,9: покупка в отделении, предзаказ в приложении."
    else:
        body = "подобрали продукты, которые дадут больше выгоды от повседневных расходов."

    text = f"{hook}: {body} {cta}"
    # целевая длина
    if len(text) < 180:
        add = rng.choice([
            " Всё в приложении за пару минут.",
            " Без лишних шагов — за 1–2 минуты.",
            " Условия прозрачные и без мелкого шрифта.",
            " Вы всегда можете отключить в один тап."
        ])
        text = text + add
    return text

# ================== ИИ-генератор пуша (с вариативным стилем) ==================
def generate_push_ai(api_key:str, temperature:float, signals:dict, base_text:str) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    cta_map = {
        P_TRAVEL: ["Открыть карту.", "Подключить карту.", "Начать пользоваться."],
        P_PREM: ["Оформить сейчас.", "Подключить премиум.", "Получить привилегии."],
        P_CC: ["Оформить карту.", "Получить карту.", "Подать заявку."],
        P_FX: ["Настроить обмен.", "Выбрать авто-покупку.", "Открыть курс-цель."],
        P_DEP_SAVE: ["Открыть вклад.", "Начать копить.", "Перевести остаток."],
        P_DEP_ACC: ["Открыть вклад.", "Начать копить к цели.", "Подключить автопополнение."],
        P_DEP_MULTI: ["Открыть вклад.", "Разложить по валютам.", "Посмотреть ставки."],
        P_INV: ["Открыть брокерский счёт.", "Начать инвестировать.", "Попробовать с мини-суммы."],
        P_GOLD: ["Посмотреть детали.", "Оформить предзаказ.", "Узнать условия."],
        P_CASH: ["Узнать лимит.", "Оформить заявку.", "Проверить предложения."]
    }
    cta_options = cta_map.get(signals["best_product"], ["Посмотреть детали."])
    style_seed = int(signals.get("client_code", 0)) % 10000
    sys_prompt = (
        "Ты редактор-ассистент банка. Верни ОДИН короткий пуш 180–220 символов: хук → польза → CTA."
        " На «вы», дружелюбно, без капса/давления. Формат валюты: 27 400 ₸. "
        "Вариативность: различай формулировки между клиентами (меняй хук, порядок, синонимы), но сохраняй суть. "
        "Стиль фиксируй по style_id для детерминизма. Не повторяй слово в слово входной текст."
    )
    ctx = {
        "style_id": style_seed,
        "best_product": signals.get("best_product"),
        "benefit_best_month": int(round(signals.get("benefit_best", 0.0))),
        "name": signals.get("name"),
        "age": signals.get("age"),
        "city": signals.get("city"),
        "month": signals.get("month_prep"),
        "top_categories": signals.get("top_categories"),
        "taxi_count": signals.get("taxi_count", 0),
        "avg_balance": int(round(signals.get("avg_balance", 0))),
        "cta_choices": cta_options
    }
    user_prompt = (
        "Перефразируй и улучшай этот текст под контекст. Сохрани смысл и CTA из списка:\n"
        f"BASE: {base_text}\n"
        f"CONTEXT: {ctx}\n"
        "Верни только финальный пуш."
    )
    ans = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"system","content":sys_prompt},{"role":"user","content":user_prompt}],
        temperature=float(temperature), max_tokens=200
    )
    return clamp_push(ans.choices[0].message.content.strip())

# ================== Локальные быстрые ответы ==================
def answer_local(msg:str):
    low = msg.lower().strip()

    if re.search(r"\bсколько клиентов\b", low):
        if st.session_state.clients is None: return "Сначала загрузите ZIP и нажмите «Проанализировать»."
        return f"Клиентов в датасете: {len(st.session_state.clients)}."

    if re.search(r"\b(средн(ий|ая)\s+баланс|average balance)\b", low):
        if st.session_state.clients is None: return "Нет данных — загрузите ZIP."
        v = st.session_state.clients.get("avg_monthly_balance_KZT", pd.Series(dtype=float)).mean()
        return f"Средний баланс по клиентам: {fmt_int_kzt(v)}."

    m = re.search(r"по продукту\s+(.+)$", low)
    if m and st.session_state.df is not None:
        q = m.group(1).strip()
        sub = st.session_state.df[st.session_state.df['product'].str.contains(q, case=False, na=False)]
        return f"{len(sub)} клиентов с продуктом, содержащим «{q}»."

    if low in ("часто", "топ продукт", "какой продукт чаще всего"):
        if st.session_state.df is None: return "Нет данных — загрузите ZIP."
        top = st.session_state.df['product'].value_counts().head(3)
        lines = [f"• {k}: {v}" for k,v in top.items()]
        return "Самые частые продукты:\n" + "\n".join(lines)

    m = re.search(r"push\s+(\d+)", low)
    if m and st.session_state.df is not None:
        code = int(m.group(1))
        row = st.session_state.df[st.session_state.df["client_code"]==code]
        if len(row)==0: return "Клиент не найден."
        r = row.iloc[0]
        return f"{r['client_code']}: {r['product']}\n{r['push_notification']}"

    m = re.search(r"(сигналы|категории)\s+(\d+)", low)
    if m:
        code = int(m.group(2))
        if 'signals' not in st.session_state or code not in st.session_state.signals:
            return "Нет сигналов — перезапустите анализ."
        s = st.session_state.signals[code]
        return ("Сигналы клиента "
                f"{code}: топ-категории={s.get('top_categories')}, такси={s.get('taxi_count')}, "
                f"онлайн={int(s.get('online_3m',0))}, путешествия={int(s.get('travel_spend_3m',0))}, "
                f"баланс={fmt_int_kzt(s.get('avg_balance',0))}.")

    return None  # не распознано — пусть обработают остальные ветки

# ================== Свободные вопросы (фильтры/агрегаты/списки) ==================
def _num_in(text, key, default=None):
    m = re.search(rf"{key}\s*([<>]=?|=)\s*([\d\s]+)", text)
    if not m: return None
    op, val = m.group(1), int(re.sub(r"\s+","", m.group(2)))
    return op, val

def _apply_num_filter(df, col, cond):
    if not cond: return df
    op, v = cond
    if op == ">":  return df[df[col] > v]
    if op == "<":  return df[df[col] < v]
    if op == ">=": return df[df[col] >= v]
    if op == "<=": return df[df[col] <= v]
    return df[df[col] == v]

def answer_freeform(msg:str):
    if st.session_state.get("tbl") is None:
        return None
    text = msg.strip()
    low  = text.lower()
    tbl  = st.session_state.tbl.copy()

    # город
    m_city = re.search(r"(город|из)\s+([A-Za-zА-Яа-яЁё\-\s]+)", low)
    if m_city:
        city = m_city.group(2).strip().title()
        if "city" in tbl.columns:
            tbl = tbl[tbl["city"].fillna("").str.title() == city]

    # статус
    m_status = re.search(r"(статус|status)\s+([A-Za-zА-Яа-яЁё\-\s]+)", low)
    if m_status and "status" in tbl.columns:
        stq = m_status.group(2).strip().lower()
        tbl = tbl[tbl["status"].fillna("").str.lower().str.contains(stq)]

    # числовые фильтры
    age_cond = _num_in(low, r"(возраст|age)")
    bal_cond = _num_in(low, r"(баланс|balance|avg_balance)")
    if "age" in tbl.columns:
        tbl = _apply_num_filter(tbl, "age", age_cond)
    if "avg_balance_kzt" in tbl.columns:
        tbl = _apply_num_filter(tbl, "avg_balance_kzt", bal_cond)

    taxi_cond   = _num_in(low, r"(такси|taxi)")
    online_cond = _num_in(low, r"(онлайн|online)")
    travel_cond = _num_in(low, r"(путешеств|travel)")
    if "taxi_count" in tbl.columns:
        tbl = _apply_num_filter(tbl, "taxi_count", taxi_cond)
    if "online_3m" in tbl.columns:
        tbl = _apply_num_filter(tbl, "online_3m", online_cond)
    if "travel_spend_3m" in tbl.columns:
        tbl = _apply_num_filter(tbl, "travel_spend_3m", travel_cond)

    # фильтр по выбранному продукту
    m_prod = re.search(r"(продукт|product|карта|вклад|инвестиции)\s*[:=]?\s+(.+)$", low)
    if m_prod and "product" in tbl.columns:
        q = m_prod.group(2).strip()
        tbl = tbl[tbl["product"].fillna("").str.contains(q, case=False, na=False)]

    # агрегаты
    if re.search(r"\b(сколько|count|кол-во)\b", low):
        return f"Под условие подходит клиентов: {len(tbl)}."

    if re.search(r"\b(средн(ее|ий)|average|avg)\s+(баланс|balance)\b", low):
        if len(tbl)==0: return "Нет клиентов под условие."
        v = tbl["avg_balance_kzt"].mean() if "avg_balance_kzt" in tbl.columns else None
        return "Средний баланс: " + (fmt_int_kzt(v) if v is not None else "н/д")

    if re.search(r"(распределени|топ|част(ые|ота))\s+по\s+продукт", low) and "product" in tbl.columns:
        if len(tbl)==0: return "Нет клиентов под условие."
        vc = tbl["product"].value_counts().head(10)
        lines = [f"• {k}: {v}" for k,v in vc.items()]
        return "Распределение по продуктам (топ-10):\n" + "\n".join(lines)

    # список первых N
    m_list = re.search(r"(покажи|список|list)(?:\s+(\d+))?", low)
    if m_list:
        n = int(m_list.group(2)) if m_list.group(2) else 20
        cols = [c for c in ["client_code","name","age","city","status","product"] if c in tbl.columns]
        if len(tbl)==0: return "Нет клиентов под условие."
        preview = tbl[cols].head(n).to_string(index=False)
        return f"Первые {min(n,len(tbl))} клиентов под условие:\n```\n{preview}\n```"

    # пуш/почему для конкретного client_code
    m_push = re.search(r"(push|пуш|уведом\w*)\s+(\d+)", low)
    if m_push:
        code = int(m_push.group(2))
        row = st.session_state.df[st.session_state.df["client_code"]==code]
        if len(row)==0: return "Клиент не найден."
        r = row.iloc[0]
        return f"{r['client_code']}: {r['product']}\n{r['push_notification']}"

    # объяснение логики
    if re.search(r"(объясни|как работ(ае|)т)\s+(скоринг|scoring|ранжир|логик)", low):
        return ("Мы считаем сигналы (топ-категории трат, онлайн-платежи, частота такси/поездок, FX-оборот, "
                "средний баланс, зарплатные поступления), оцениваем ожидаемую выгоду по каждому продукту, "
                "добавляем логарифм выгоды к скору и ранжируем. Лучший продукт — с максимальным суммарным скором.")

    return None

# ================== STATE ==================
if "df" not in st.session_state: st.session_state.df = None
if "ranks" not in st.session_state: st.session_state.ranks = {}
if "clients" not in st.session_state: st.session_state.clients = None
if "chat" not in st.session_state: st.session_state.chat = []
if "signals" not in st.session_state: st.session_state.signals = {}
if "tbl" not in st.session_state: st.session_state.tbl = None

# ================== SIDEBAR ==================
with st.sidebar:
    st.markdown("### ⚙️ Настройки / Загрузка")
    uploaded = st.file_uploader("Загрузите dataset ZIP (case 1.zip)", type=["zip"])
    st.markdown('<span class="muted">Структура: case 1/clients.csv и client_*_transactions/transfers</span>', unsafe_allow_html=True)
    st.divider()
    st.markdown("### 🧠 Текст пуша")
    use_ai_push = st.checkbox("Генерировать пуш через ИИ", value=True)
    temp = st.slider("Креативность (temperature)", 0.0, 1.0, 0.2, 0.1)
    analyze = st.button("🔎 Проанализировать")

# ================== MAIN LAYOUT ==================
left, right = st.columns([0.52, 0.48], gap="large")

with left:
    st.markdown("### 💬 ИИ-агент")
    if not st.session_state.chat:
        st.session_state.chat.append(("assistant",
            "Загрузите ZIP → «Проанализировать».\n"
            "Примеры свободных вопросов:\n"
            "• Сколько клиентов из Алматы возраст > 50 с балансом > 1 000 000?\n"
            "• Покажи 15 клиентов статус студент с онлайн > 100 000\n"
            "• Распределение по продуктам\n"
            "• push 27 / почему 27\n"
            "Также: почему <id>, top <id>/top4 <id>, найти <product>."
        ))

    if analyze:
        if not uploaded:
            st.session_state.chat.append(("assistant", "Не вижу ZIP. Пожалуйста, добавьте файл слева."))
        else:
            try:
                zf = zipfile.ZipFile(io.BytesIO(uploaded.read()))
                clients = read_clients(zf)
                rows, ranks, all_signals = [], {}, {}
                api_key = os.getenv("OPENAI_API_KEY", "")
                for _, prof in clients.iterrows():
                    code = int(prof['client_code'])
                    tx, tr = read_client_frames(zf, code)
                    product, push_local, ranked, benefits, signals = pick_best_and_push(prof, tx, tr)

                    # вариативный локальный пуш готов, опционально — перефраз ИИ
                    final_push = push_local
                    if use_ai_push and api_key:
                        try:
                            candidate = generate_push_ai(api_key, temp, signals, base_text=push_local)
                            if candidate and len(candidate) >= 170:
                                final_push = candidate
                        except Exception:
                            pass

                    ranks[code] = ranked
                    all_signals[code] = signals
                    rows.append((code, product, final_push))

                main_df = pd.DataFrame(rows, columns=['client_code','product','push_notification'])

                # === СВОДНАЯ ТАБЛИЦА ДЛЯ СВОБОДНЫХ ВОПРОСОВ ===
                sig_df = pd.DataFrame.from_dict(all_signals, orient="index")
                if "client_code" not in sig_df.columns:
                    sig_df["client_code"] = sig_df.index
                tbl = (clients.merge(sig_df, on="client_code", how="left")
                              .merge(main_df, on="client_code", how="left"))
                if "avg_monthly_balance_KZT" in tbl.columns:
                    tbl = tbl.rename(columns={"avg_monthly_balance_KZT":"avg_balance_kzt"})

                st.session_state.df = main_df
                st.session_state.ranks = ranks
                st.session_state.clients = clients
                st.session_state.signals = all_signals
                st.session_state.tbl = tbl

                st.session_state.chat.append(("assistant", "Готово ✅ Рекомендации рассчитаны. Справа — таблица и кнопка скачивания."))
            except Exception as e:
                st.session_state.chat.append(("assistant", f"Ошибка при разборе ZIP: {e}"))

    for role, text in st.session_state.chat:
        st.chat_message(role).write(text)

    user_msg = st.chat_input("Напишите сообщение агенту…")
    if user_msg:
        st.chat_message("user").write(user_msg)
        msg, low = user_msg.strip(), user_msg.strip().lower()
        reply = None

        # локальные быстрые ответы (без API)
        reply = answer_local(msg) or reply

        # свободные вопросы (фильтры/агрегаты/списки)
        if reply is None:
            reply = answer_freeform(msg)

        # top4
        if reply is None and low.startswith("top4"):
            parts = re.findall(r"\d+", low)
            if parts:
                code = int(parts[0])
                if code in st.session_state.ranks:
                    r = st.session_state.ranks[code][:4]
                    reply = "Топ-4 продуктов для клиента " + str(code) + ":\n" + "\n".join([f"• {p} — {round(s,2)}" for p,s in r])
                else: reply = "Нет данных для этого client_code."
            else: reply = "Формат: `top4 12`"

        # top
        if reply is None and re.match(r"^top\b", low):
            nums = re.findall(r"\d+", low)
            if nums:
                code = int(nums[0])
                if code in st.session_state.ranks:
                    full = st.session_state.ranks[code]
                    reply = "Полный рейтинг продуктов для клиента " + str(code) + ":\n" + "\n".join([f"{i}. {p} — {round(s,2)}" for i,(p,s) in enumerate(full,1)])
                else: reply = "Нет данных для этого client_code."
            else:
                reply = "Укажите client_code: `top 12`"

        # найти
        if reply is None and (low.startswith("найди") or low.startswith("find")):
            q = msg[5:].strip() if low.startswith("найди") else msg[4:].strip()
            if st.session_state.df is None: reply = "Сначала загрузите ZIP и нажмите «Проанализировать»."
            elif not q: reply = "Формат: `найди Премиальная карта`"
            else:
                sub = st.session_state.df[st.session_state.df['product'].str.contains(q, case=False, na=False)]
                if len(sub)==0: reply = f"Клиенты с продуктом, содержащим «{q}», не найдены."
                else:
                    preview = sub[['client_code','product']].head(20).to_string(index=False)
                    reply = f"Найдено {len(sub)} клиентов. Первые 20:\n```\n{preview}\n```"

        # почему
        if reply is None and (low.startswith("почему") or low.startswith("why")):
            nums = re.findall(r"\d+", low)
            if not nums: reply = "Укажите client_code: `почему 12`"
            else:
                code = int(nums[0])
                if st.session_state.df is None or st.session_state.clients is None or code not in st.session_state.ranks:
                    reply = "Нет данных для этого client_code. Загрузите ZIP и проанализируйте."
                else:
                    best_row = st.session_state.df[st.session_state.df["client_code"]==code].iloc[0]
                    best_product = best_row["product"]
                    ranked = st.session_state.ranks[code]
                    s = st.session_state.signals.get(code, {})
                    api_key = os.getenv("OPENAI_API_KEY", "")
                    if not api_key:
                        # локальное короткое объяснение
                        top4 = "\n".join([f"{i}. {p}" for i,(p,_) in enumerate(ranked[:4],1)])
                        why_bits = []
                        if best_product == P_TRAVEL and s.get("taxi_count",0)>0: why_bits.append(f"частые такси ({s.get('taxi_count')})")
                        if best_product == P_CC and s.get("top_categories"): why_bits.append(f"топ-категории: {', '.join(s.get('top_categories')[:3])}")
                        if best_product == P_PREM and s.get("avg_balance",0)>1_000_000: why_bits.append("высокий средний остаток")
                        hint = ("; ".join(why_bits)) if why_bits else "сигналы расходов и ожидаемая выгода"
                        reply = (f"Лучший продукт для {code}: {best_product} — благодаря сигналам ({hint}).\n"
                                 f"Альтернативы:\n{top4}")
                    else:
                        try:
                            from openai import OpenAI
                            client = OpenAI(api_key=api_key)
                            sys_prompt = ("Ты — продуктовый аналитик. Объясни выбор банковского продукта по ранжированию. "
                                          "Кратко, по делу, на «вы», укажи ключевые сигналы и 1–2 альтернативы из топа.")
                            messages = [
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": f"Лучший продукт: {best_product}. Топ: {ranked[:6]}. Сигналы: {s}."}
                            ]
                            ans = client.chat.completions.create(model="gpt-4o-mini", messages=messages, temperature=0.2)
                            reply = ans.choices[0].message.content.strip()
                        except Exception as e:
                            reply = f"Не удалось обратиться к OpenAI API: {e}"

        # общий фоллбек
        if reply is None:
            api_key = os.getenv("OPENAI_API_KEY", "")
            if api_key and (st.session_state.df is not None):
                try:
                    sample = st.session_state.df.head(8).to_dict(orient="records")
                    from openai import OpenAI
                    client = OpenAI(api_key=api_key)
                    system = ("Ты — помощник аналитика продукта. Отвечай на любые вопросы про датасет, рекомендации и логику пушей. Кратко и ясно.")
                    ans = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=[{"role":"system","content":system},
                                  {"role":"user","content":f"Вопрос: {msg}\nПример данных: {sample}"}],
                        temperature=0.3)
                    reply = ans.choices[0].message.content.strip()
                except Exception as e:
                    reply = f"Не удалось ответить через OpenAI API: {e}"
            else:
                reply = ("Готов отвечать на вопросы. Команды: `почему <id>`, `top <id>`, `top4 <id>`, "
                         "`найди <product>`, `push <id>`, `сигналы <id>`, `сколько клиентов`, `средний баланс`, "
                         "`по продукту <название>`.\nПишите и свободные вопросы — фильтры по городу/статусу/возрасту/балансу/категориям.")

        st.session_state.chat.append(("assistant", reply))
        st.rerun()

with right:
    st.markdown("### 📊 Результаты")
    if st.session_state.df is None:
        st.info("Пока нет данных. Загрузите ZIP слева и нажмите «Проанализировать».")
    else:
        st.container(border=True).dataframe(st.session_state.df, use_container_width=True, height=520)
        csv_bytes = st.session_state.df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Скачать CSV", data=csv_bytes, file_name="case1_recommendations.csv", mime="text/csv")
        st.caption("CSV: client_code, product, push_notification.")
