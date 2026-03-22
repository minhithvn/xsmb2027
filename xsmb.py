"""
XSMB Analytics – phân tích toàn diện lô tô + số đề
Fetch 365 ngày thực, tìm quy luật thực sự, bỏ back-test.
"""
import datetime
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
import streamlit as st
from bs4 import BeautifulSoup

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
SOURCES = [
    "https://www.minhngoc.net.vn/ket-qua-xo-so/mien-bac/{}.html",
    "https://xoso.com.vn/xsmb-{}.html",
]
CACHE_PATH = Path(".cache/xsmb_cache.json")
CACHE_PATH.parent.mkdir(exist_ok=True)
ALL_NUMS  = [f"{i:02d}" for i in range(100)]
WD_NAMES  = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
HISTORY_DAYS = 365   # luôn lấy 365 ngày gần nhất tính từ hôm nay (động)


# ─────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────
def _load_cache() -> Dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _save_cache(cache: Dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def get_cached(date: datetime.date) -> Optional[Dict]:
    entry = _load_cache().get(date.isoformat())
    return entry.get("data") if entry else None

def set_cached(date: datetime.date, data: Dict) -> None:
    cache = _load_cache()
    cache[date.isoformat()] = {"data": data, "ts": int(time.time())}
    _save_cache(cache)


# ─────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────
def _fetch_url(url: str, retries: int = 2) -> str:
    last_err: Exception = RuntimeError("no attempt")
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            r.raise_for_status()
            return r.text
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.6 * (2 ** attempt))
    raise last_err

def _from_minhngoc(date: datetime.date) -> Dict:
    url = SOURCES[0].format(date.strftime("%d-%m-%Y"))
    soup = BeautifulSoup(_fetch_url(url), "html.parser")
    tbl = soup.find("table", class_="bkqmienbac")
    if not tbl:
        raise ValueError("minhngoc: không tìm thấy bảng")
    css_map = {
        "giaidb": "GDB", "giai1": "G1", "giai2": "G2", "giai3": "G3",
        "giai4": "G4", "giai5": "G5", "giai6": "G6", "giai7": "G7",
    }
    out: Dict[str, List[str]] = {}
    for css, label in css_map.items():
        nums = [c.get_text(strip=True) for c in tbl.select(f"td.{css} div") if c.get_text(strip=True)]
        if nums:
            out[label] = nums
    if not out:
        raise ValueError("minhngoc: parse thất bại")
    return out

def _from_xoso(date: datetime.date) -> Dict:
    url = SOURCES[1].format(date.strftime("%d-%m-%Y"))
    soup = BeautifulSoup(_fetch_url(url), "html.parser")
    id_map = {
        "prizeDB": "GDB", "prize1": "G1", "prize2": "G2", "prize3": "G3",
        "prize4": "G4", "prize5": "G5", "prize6": "G6", "prize7": "G7",
    }
    out: Dict[str, List[str]] = {}
    for pid, key in id_map.items():
        cell = soup.find(id=pid)
        if cell:
            nums = [n.strip() for n in cell.get_text(" ").split() if n.strip().isdigit()]
            if nums:
                out[key] = nums
    if not out:
        raise ValueError("xoso.com.vn: parse thất bại")
    return out

def fetch_xsmb(date: datetime.date, use_cache: bool = True, force: bool = False) -> Dict:
    if use_cache and not force:
        cached = get_cached(date)
        if cached:
            return cached
    errors = []
    for fn in (_from_minhngoc, _from_xoso):
        try:
            data = fn(date)
            if use_cache:
                set_cached(date, data)
            return data
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError(" | ".join(errors))


# ─────────────────────────────────────────────
# DATA EXTRACTION
# ─────────────────────────────────────────────
def all_last2(raw: Dict) -> List[str]:
    return [n[-2:].zfill(2) for arr in raw.values() for n in arr if n]

def gdb_last2(raw: Dict) -> Optional[str]:
    arr = raw.get("GDB", [])
    return arr[0][-2:].zfill(2) if arr and arr[0] else None

def gdb_full(raw: Dict) -> Optional[str]:
    arr = raw.get("GDB", [])
    return arr[0] if arr else None


# ─────────────────────────────────────────────
# LOAD HISTORY – luôn 365 ngày tính từ hôm nay
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def load_full_history(today_str: str, force_today: bool = False) -> Tuple[List[Dict], List[datetime.date]]:
    """
    Tham số today_str (vd "2026-03-22") để st.cache_data tự invalidate khi
    sang ngày mới – đảm bảo luôn dùng dữ liệu mới nhất.

    Hai tầng cache:
      • Disk JSON (.cache/xsmb_cache.json): mỗi ngày fetch 1 lần, tồn tại
        sau khi tắt/mở lại app – lần sau chỉ tải ngày chưa có trong cache.
      • RAM (st.cache_data, TTL 1h): tránh tính lại trong cùng phiên làm việc.

    force_today=True: bỏ qua disk cache cho ngày hôm nay (dùng khi kết quả
    hôm nay vừa có mà app chưa cập nhật).
    """
    today = datetime.date.fromisoformat(today_str)
    raws, dates = [], []
    bar = st.progress(0, text="Đang tải dữ liệu lịch sử…")
    for i in range(HISTORY_DAYS):
        d = today - datetime.timedelta(days=i)
        force = (force_today and i == 0)
        cached_hit = (get_cached(d) is not None) and not force
        try:
            raw = fetch_xsmb(d, use_cache=True, force=force)
            raws.append(raw)
            dates.append(d)
        except Exception:
            pass
        bar.progress(
            min((i + 1) / HISTORY_DAYS, 1.0),
            text=f"{'[cache]' if cached_hit else '[web]  '} {d.strftime('%d/%m/%Y')}",
        )
    bar.empty()
    # raws[0] = hôm nay (mới nhất), raws[-1] = 365 ngày trước
    return raws, dates


# ─────────────────────────────────────────────
# CORE ANALYTICS
# ─────────────────────────────────────────────
def _normalize(d: Dict[str, float]) -> Dict[str, float]:
    max_v = max(d.values(), default=1) or 1
    return {k: v / max_v for k, v in d.items()}

def compute_gan_lo(history: List[Dict]) -> Dict[str, int]:
    """history[0] = newest. Trả về số ngày kể từ lần cuối xuất hiện."""
    last_seen: Dict[str, Optional[int]] = {n: None for n in ALL_NUMS}
    for offset, raw in enumerate(history):
        for num in set(all_last2(raw)):
            if last_seen[num] is None:
                last_seen[num] = offset
    return {n: (v if v is not None else len(history)) for n, v in last_seen.items()}

def compute_gan_de(history: List[Dict]) -> Dict[str, int]:
    last_seen: Dict[str, Optional[int]] = {n: None for n in ALL_NUMS}
    for offset, raw in enumerate(history):
        g = gdb_last2(raw)
        if g and last_seen[g] is None:
            last_seen[g] = offset
    return {n: (v if v is not None else len(history)) for n, v in last_seen.items()}

def freq_table_lo(history: List[Dict]) -> Dict[str, int]:
    freq: Dict[str, int] = defaultdict(int)
    for raw in history:
        for n in all_last2(raw):
            freq[n] += 1
    return dict(freq)

def freq_table_de(history: List[Dict]) -> Dict[str, int]:
    freq: Dict[str, int] = defaultdict(int)
    for raw in history:
        g = gdb_last2(raw)
        if g:
            freq[g] += 1
    return dict(freq)

def freq_by_weekday_lo(history: List[Dict], dates: List[datetime.date]) -> Dict[int, Dict[str, int]]:
    wd_freq: Dict[int, Dict[str, int]] = {i: defaultdict(int) for i in range(7)}
    for raw, d in zip(history, dates):
        wd = d.weekday()
        for n in set(all_last2(raw)):
            wd_freq[wd][n] += 1
    return wd_freq

def freq_by_weekday_de(history: List[Dict], dates: List[datetime.date]) -> Dict[int, Dict[str, int]]:
    wd_freq: Dict[int, Dict[str, int]] = {i: defaultdict(int) for i in range(7)}
    for raw, d in zip(history, dates):
        g = gdb_last2(raw)
        if g:
            wd_freq[d.weekday()][g] += 1
    return wd_freq

def pair_freq(history: List[Dict], top_n: int = 20) -> List[Tuple[str, str, int]]:
    counter: Dict[Tuple[str, str], int] = defaultdict(int)
    for raw in history:
        nums = sorted(set(all_last2(raw)))
        for i in range(len(nums)):
            for j in range(i + 1, len(nums)):
                counter[(nums[i], nums[j])] += 1
    return [(a, b, c) for (a, b), c in sorted(counter.items(), key=lambda x: -x[1])[:top_n]]

def detect_cycle(seq: List[Optional[str]], target: str) -> Tuple[float, float, int]:
    positions = [i for i, v in enumerate(seq) if v == target]
    if len(positions) < 2:
        return 9999.0, 0.0, len(positions)
    intervals = [positions[i + 1] - positions[i] for i in range(len(positions) - 1)]
    avg = sum(intervals) / len(intervals)
    std = math.sqrt(sum((x - avg) ** 2 for x in intervals) / len(intervals)) if len(intervals) > 1 else avg
    consistency = max(0.0, 1.0 - std / avg) if avg > 0 else 0.0
    return avg, consistency, len(positions)

def avg_gan_real(history: List[Dict]) -> Dict[str, float]:
    """Khoảng cách trung bình thực tế giữa các lần xuất hiện (lô)."""
    last: Dict[str, Optional[int]] = {n: None for n in ALL_NUMS}
    intervals: Dict[str, List[int]] = {n: [] for n in ALL_NUMS}
    for offset, raw in enumerate(history):
        for n in set(all_last2(raw)):
            if last[n] is not None:
                intervals[n].append(offset - last[n])
            last[n] = offset
    return {n: (sum(v) / len(v) if v else 0.0) for n, v in intervals.items()}

def streak_analysis(history: List[Dict]) -> Dict[str, int]:
    best: Dict[str, int] = {n: 0 for n in ALL_NUMS}
    cur:  Dict[str, int] = {n: 0 for n in ALL_NUMS}
    for raw in reversed(history):   # oldest first for streak
        appeared = set(all_last2(raw))
        for n in ALL_NUMS:
            if n in appeared:
                cur[n] += 1
                best[n] = max(best[n], cur[n])
            else:
                cur[n] = 0
    return best


# ─────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────
def lo_score(history: List[Dict], decay: float = 0.88) -> Dict[str, float]:
    if not history:
        return {n: 0.0 for n in ALL_NUMS}

    # A. Frequency (30 ngày gần, decay)
    freq: Dict[str, float] = {n: 0.0 for n in ALL_NUMS}
    for offset, raw in enumerate(history[:30]):
        w = decay ** offset
        for num in all_last2(raw):
            freq[num] += w

    # B. Overdue vs expected gap thực tế
    expected_gap = avg_gan_real(history)
    gan = compute_gan_lo(history)
    overdue: Dict[str, float] = {}
    for n in ALL_NUMS:
        eg = expected_gap.get(n, 4.0) or 4.0
        ratio = gan[n] / eg
        if ratio < 0.5:
            overdue[n] = 0.0
        elif ratio < 0.9:
            overdue[n] = 0.2
        elif ratio < 1.2:
            overdue[n] = 0.5
        elif ratio < 2.0:
            overdue[n] = 0.85
        else:
            overdue[n] = 1.0

    # C. Recency hot (3 ngày gần)
    recency: Dict[str, float] = {n: 0.0 for n in ALL_NUMS}
    for offset, raw in enumerate(history[:3]):
        bonus = (3 - offset) / 3.0
        for num in set(all_last2(raw)):
            recency[num] += bonus

    # D. Pair boost – partner hay đi cùng đã về hôm qua
    pair_boost: Dict[str, float] = {n: 0.0 for n in ALL_NUMS}
    if len(history) >= 2:
        yesterday = set(all_last2(history[1]))
        for a, b, cnt in pair_freq(history, top_n=200):
            strength = cnt / len(history)
            if a in yesterday:
                pair_boost[b] = max(pair_boost[b], strength)
            if b in yesterday:
                pair_boost[a] = max(pair_boost[a], strength)

    f = _normalize(freq)
    o = _normalize(overdue)
    r = _normalize(recency)
    p = _normalize(pair_boost)

    return {n: 0.30*f[n] + 0.40*o[n] + 0.15*r[n] + 0.15*p[n] for n in ALL_NUMS}


def de_score(history: List[Dict], dates: List[datetime.date]) -> Dict[str, float]:
    if not history:
        return {n: 0.0 for n in ALL_NUMS}

    gdb_seq = [gdb_last2(raw) for raw in history]
    gan     = compute_gan_de(history)

    avg_cycles:    Dict[str, float] = {}
    consistencies: Dict[str, float] = {}
    for n in ALL_NUMS:
        avg, cons, _ = detect_cycle(gdb_seq, n)
        avg_cycles[n]    = avg
        consistencies[n] = cons

    # A. Overdue vs own cycle
    overdue: Dict[str, float] = {}
    for n in ALL_NUMS:
        cyc = avg_cycles[n]
        g   = gan[n]
        ratio = g / cyc if cyc < 500 else g / 100.0
        if ratio < 0.6:
            overdue[n] = 0.0
        elif ratio < 0.85:
            overdue[n] = 0.20
        elif ratio < 1.05:
            overdue[n] = 0.60
        elif ratio < 1.50:
            overdue[n] = 0.90
        else:
            overdue[n] = 1.00

    # B. Frequency GDB (decay 0.97)
    freq: Dict[str, float] = {n: 0.0 for n in ALL_NUMS}
    for offset, n in enumerate(gdb_seq):
        if n:
            freq[n] += (0.97 ** offset)

    # C. Cycle timing
    cycle_s: Dict[str, float] = {n: 0.0 for n in ALL_NUMS}
    for n in ALL_NUMS:
        cons = consistencies[n]
        cyc  = avg_cycles[n]
        if cons < 0.25 or cyc >= 500:
            continue
        dev = abs(gan[n] - cyc) / cyc
        if dev <= 0.15:
            cycle_s[n] = cons * 1.0
        elif dev <= 0.30:
            cycle_s[n] = cons * 0.7
        elif gan[n] > cyc:
            cycle_s[n] = cons * 0.4

    # D. Weekday bonus
    next_wd = (dates[0] + datetime.timedelta(days=1)).weekday() if dates else 0
    wd_freq_de = freq_by_weekday_de(history, dates)
    wd_counts  = wd_freq_de.get(next_wd, {})
    wd_total   = sum(wd_counts.values()) or 1
    wd_bonus: Dict[str, float] = {n: wd_counts.get(n, 0) / wd_total for n in ALL_NUMS}

    o = _normalize(overdue)
    f = _normalize(freq)
    c = _normalize(cycle_s)
    w = _normalize(wd_bonus)

    return {n: 0.50*o[n] + 0.20*f[n] + 0.20*c[n] + 0.10*w[n] for n in ALL_NUMS}


# ─────────────────────────────────────────────
# STREAMLIT UI
# ─────────────────────────────────────────────
st.set_page_config(page_title="XSMB Analytics", page_icon="🎟️", layout="wide")
st.title("🎟️ XSMB – Phân tích Quy luật & Dự đoán")

TODAY = datetime.date.today()
start_display = TODAY - datetime.timedelta(days=HISTORY_DAYS - 1)
st.caption(
    f"Luôn lấy **{HISTORY_DAYS} ngày gần nhất** "
    f"({start_display:%d/%m/%Y} → {TODAY:%d/%m/%Y}) · "
    "Nguồn: minhngoc.net.vn (chính) · xoso.com.vn (dự phòng)"
)

# Nút làm mới kết quả hôm nay (sidebar)
force_today = st.sidebar.button(
    "🔄 Làm mới kết quả hôm nay",
    help="Fetch lại từ web cho ngày hôm nay – dùng khi kết quả vừa có mà app chưa cập nhật",
)
if force_today:
    st.cache_data.clear()

with st.spinner(f"Đang tải {HISTORY_DAYS} ngày dữ liệu…"):
    ALL_HISTORY, ALL_DATES = load_full_history(TODAY.isoformat(), force_today)

if not ALL_HISTORY:
    st.error("Không tải được dữ liệu. Kiểm tra kết nối mạng.")
    st.stop()

st.success(
    f"✅ Đã tải **{len(ALL_HISTORY)}** ngày · "
    f"Mới nhất: **{ALL_DATES[0]:%d/%m/%Y}** · "
    f"Cũ nhất: **{ALL_DATES[-1]:%d/%m/%Y}** · "
    f"Cache: `.cache/xsmb_cache.json`"
)

lo_tab, de_tab, insight_tab = st.tabs(["🔢 Lô Tô", "🎯 Số Đề (GDB)", "🔬 Quy luật & Thống kê"])


# ══════════════════════════════════════════════
# TAB 1 – LÔ TÔ
# ══════════════════════════════════════════════
with lo_tab:
    st.markdown("""
**Điểm tổng hợp 4 quy luật từ dữ liệu thực tế:**

| Quy luật | Trọng số | Ý nghĩa |
|---|---|---|
| Gan vs kỳ vọng thực | **40%** | So sánh gan hiện tại với khoảng cách TB thực tế của chính số đó |
| Tần suất 30 ngày gần | **30%** | Decay-weighted – ưu tiên ngày gần |
| Cặp đôi (pair boost) | **15%** | Partner hay đi cùng đã về hôm qua → boost hôm nay |
| Recency hot | **15%** | Đang trong chuỗi về 1-3 ngày gần |
    """)

    c1, c2, c3 = st.columns(3)
    with c1:
        lo_date = st.date_input("Ngày dữ liệu cuối", datetime.date.today(), key="lo_d")
    with c2:
        lo_topk = st.slider("Top K số gợi ý", 3, 20, 10, key="lo_k")
    with c3:
        lo_hist = st.slider("Số ngày lịch sử dùng để score",
                            30, min(365, len(ALL_HISTORY)),
                            min(180, len(ALL_HISTORY)), key="lo_h")

    if st.button("▶ Gợi ý Lô Tô", key="btn_lo"):
        hist_slice = [(r, d) for r, d in zip(ALL_HISTORY, ALL_DATES) if d <= lo_date][:lo_hist]
        if not hist_slice:
            st.error("Không đủ dữ liệu.")
        else:
            h_raws  = [x[0] for x in hist_slice]
            scores  = lo_score(h_raws)
            gan     = compute_gan_lo(h_raws)
            avg_gap = avg_gan_real(h_raws)
            streaks = streak_analysis(h_raws)

            top_pairs_sorted = sorted(scores.items(), key=lambda x: -x[1])[:lo_topk]
            top_nums = [n for n, _ in top_pairs_sorted]

            next_d = lo_date + datetime.timedelta(days=1)
            st.subheader(f"Top {lo_topk} số Lô – dự đoán {next_d:%d/%m/%Y} ({WD_NAMES[next_d.weekday()]})")

            rows = []
            for rank, (num, score) in enumerate(top_pairs_sorted, 1):
                g  = gan[num]
                eg = avg_gap.get(num, 0)
                rows.append({
                    "Hạng":           rank,
                    "Số":             num,
                    "Điểm":           f"{score:.3f}",
                    "Gan (ngày)":     g,
                    "Gan TB thực":    f"{eg:.1f}",
                    "Chuỗi dài nhất": streaks[num],
                    "Trạng thái": (
                        "🔥 Quá hạn"  if eg > 0 and g / eg > 1.8
                        else "⚠️ Sắp" if eg > 0 and g / eg > 1.2
                        else "⚡ Hot"  if g <= 2
                        else "—"
                    ),
                })
            st.table(rows)
            st.success(f"✅ Ưu tiên: **{' · '.join(top_nums[:5])}**")

            st.subheader("🤝 Top 10 cặp đôi hay về cùng ngày")
            pairs = pair_freq(h_raws, top_n=10)
            st.table([
                {"Cặp": f"{a} – {b}", "Số lần": cnt, "Tỷ lệ": f"{cnt/len(h_raws):.1%}"}
                for a, b, cnt in pairs
            ])
            st.caption("Nếu 1 số trong cặp về hôm qua → số còn lại có xác suất cao hơn hôm nay")


# ══════════════════════════════════════════════
# TAB 2 – SỐ ĐỀ
# ══════════════════════════════════════════════
with de_tab:
    st.markdown("""
**Điểm tổng hợp 4 quy luật GDB thực tế:**

| Quy luật | Trọng số | Ý nghĩa |
|---|---|---|
| Gan vs chu kỳ riêng | **50%** | Mỗi số có chu kỳ TB riêng (không phải 100 ngày cố định) |
| Đúng thời điểm chu kỳ | **20%** | Gan hiện tại ≈ chu kỳ TB → boost mạnh |
| Tần suất GDB (decay) | **20%** | Lịch sử về GDB có trọng số giảm dần |
| Xác suất theo thứ | **10%** | Số hay về vào đúng thứ dự đoán |
    """)

    c1, c2, c3 = st.columns(3)
    with c1:
        de_date = st.date_input("Ngày dữ liệu cuối", datetime.date.today(), key="de_d")
    with c2:
        de_topk = st.slider("Top K số gợi ý", 1, 10, 5, key="de_k")
    with c3:
        de_hist = st.slider("Số ngày lịch sử",
                            90, min(365, len(ALL_HISTORY)),
                            min(365, len(ALL_HISTORY)), key="de_h")

    show_cycle    = st.checkbox("Hiển thị bảng chu kỳ đầy đủ (100 số)", False)
    show_gdb_hist = st.checkbox("Hiển thị lịch sử GDB 60 ngày gần nhất", False)

    if st.button("▶ Gợi ý Số Đề", key="btn_de"):
        hist_slice = [(r, d) for r, d in zip(ALL_HISTORY, ALL_DATES) if d <= de_date][:de_hist]
        if not hist_slice:
            st.error("Không đủ dữ liệu.")
        else:
            h_raws  = [x[0] for x in hist_slice]
            h_dates = [x[1] for x in hist_slice]
            scores  = de_score(h_raws, h_dates)
            gan     = compute_gan_de(h_raws)
            gdb_seq = [gdb_last2(r) for r in h_raws]

            top_pairs_sorted = sorted(scores.items(), key=lambda x: -x[1])[:de_topk]
            top_nums = [n for n, _ in top_pairs_sorted]

            next_d = de_date + datetime.timedelta(days=1)
            st.subheader(f"Top {de_topk} số Đề – dự đoán {next_d:%d/%m/%Y} ({WD_NAMES[next_d.weekday()]})")

            rows = []
            for rank, (num, score) in enumerate(top_pairs_sorted, 1):
                avg_c, cons, cnt = detect_cycle(gdb_seq, num)
                g = gan[num]
                rows.append({
                    "Hạng":         rank,
                    "Số":           num,
                    "Điểm":         f"{score:.3f}",
                    "Gan GDB":      g,
                    "Chu kỳ TB":    f"{avg_c:.0f}" if avg_c < 500 else "?",
                    "Độ đều (0-1)": f"{cons:.2f}",
                    "Số lần ĐB":    cnt,
                    "Trạng thái": (
                        "🔥 Nóng"       if g > 0 and avg_c < 500 and g / avg_c > 1.4
                        else "⚠️ Sắp"   if g > 0 and avg_c < 500 and g / avg_c > 0.85
                        else "✅ Vừa về" if g <= 3
                        else "—"
                    ),
                })
            st.table(rows)
            st.success(f"✅ Ưu tiên: **{' · '.join(top_nums)}**")

            if show_cycle:
                st.subheader("📋 Bảng chu kỳ GDB – 100 số (sắp xếp theo gan/chu kỳ)")
                cycle_rows = []
                for n in ALL_NUMS:
                    avg_c, cons, cnt = detect_cycle(gdb_seq, n)
                    g = gan[n]
                    ratio = g / avg_c if avg_c < 500 else 0
                    cycle_rows.append({
                        "Số": n, "Lần ĐB": cnt,
                        "Gan": g,
                        "Chu kỳ TB": f"{avg_c:.0f}" if avg_c < 500 else "chưa đủ",
                        "Độ đều": f"{cons:.2f}",
                        "Gan/CK": f"{ratio:.2f}" if avg_c < 500 else "—",
                        "": (
                            "🔥🔥" if ratio > 1.8
                            else "🔥" if ratio > 1.2
                            else "⚠️" if ratio > 0.85
                            else ""
                        ),
                    })
                cycle_rows.sort(key=lambda r: -gan[r["Số"]])
                st.table(cycle_rows)

            if show_gdb_hist:
                st.subheader("Lịch sử GDB 60 ngày gần nhất")
                st.table([{
                    "Ngày": d.strftime("%d/%m/%Y"),
                    "Thứ":  WD_NAMES[d.weekday()],
                    "ĐB":   gdb_full(r) or "?",
                    "2 số cuối": gdb_last2(r) or "?",
                } for r, d in zip(h_raws[:60], h_dates[:60])])


# ══════════════════════════════════════════════
# TAB 3 – INSIGHTS
# ══════════════════════════════════════════════
with insight_tab:
    st.subheader(f"🔬 Quy luật thực tế – {len(ALL_HISTORY)} ngày ({ALL_DATES[-1]:%d/%m/%Y} → {ALL_DATES[0]:%d/%m/%Y})")

    h_raws  = ALL_HISTORY
    h_dates = ALL_DATES

    with st.expander("① Tần suất lô tô – Top 20 về nhiều & ít nhất", expanded=True):
        freq = freq_table_lo(h_raws)
        sorted_freq = sorted(freq.items(), key=lambda x: -x[1])
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Top 20 về NHIỀU nhất:**")
            st.table([{"Số": n, "Lần": c, "Tỷ lệ/ngày": f"{c/len(h_raws):.2f}"}
                      for n, c in sorted_freq[:20]])
        with col2:
            st.markdown("**Top 20 về ÍT nhất:**")
            st.table([{"Số": n, "Lần": c, "Tỷ lệ/ngày": f"{c/len(h_raws):.2f}"}
                      for n, c in sorted_freq[-20:]])

    with st.expander("② Khoảng cách trung bình thực tế (lô) – mỗi số bao nhiêu ngày về 1 lần"):
        avg_gap = avg_gan_real(h_raws)
        gap_sorted = sorted(avg_gap.items(), key=lambda x: x[1])
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Gan NGẮN nhất (về đều nhất):**")
            st.table([{"Số": n, "Gan TB (ngày)": f"{g:.1f}"} for n, g in gap_sorted[:15]])
        with col2:
            st.markdown("**Gan DÀI nhất (về ít nhất):**")
            st.table([{"Số": n, "Gan TB (ngày)": f"{g:.1f}"} for n, g in gap_sorted[-15:]])

    with st.expander("③ Top 20 cặp đôi hay về cùng ngày nhất"):
        pairs = pair_freq(h_raws, top_n=20)
        st.table([{"Cặp": f"{a} – {b}", "Số lần": cnt,
                   "Tỷ lệ": f"{cnt/len(h_raws):.1%}"} for a, b, cnt in pairs])
        st.caption("Tỷ lệ X% = cứ 100 ngày thì ~X ngày cả hai cùng về")

    with st.expander("④ Top 5 lô nóng nhất từng thứ trong tuần"):
        wd_freq = freq_by_weekday_lo(h_raws, h_dates)
        wd_totals = {wd: sum(v.values()) for wd, v in wd_freq.items()}
        cols = st.columns(7)
        for wd in range(7):
            with cols[wd]:
                st.markdown(f"**{WD_NAMES[wd]}**")
                total = wd_totals[wd] or 1
                for num, c in sorted(wd_freq[wd].items(), key=lambda x: -x[1])[:5]:
                    st.write(f"{num}: {c/total:.1%}")

    with st.expander("⑤ Chuỗi lô về liên tiếp nhiều ngày nhất (streak)"):
        streaks = streak_analysis(h_raws)
        st.table([{"Số": n, "Chuỗi dài nhất (ngày)": s}
                  for n, s in sorted(streaks.items(), key=lambda x: -x[1])[:20]])

    with st.expander("⑥ Chu kỳ GDB – mỗi số bao nhiêu ngày về làm ĐB 1 lần"):
        gdb_seq = [gdb_last2(r) for r in h_raws]
        gan_de  = compute_gan_de(h_raws)
        cycle_rows = []
        for n in ALL_NUMS:
            avg_c, cons, cnt = detect_cycle(gdb_seq, n)
            if cnt < 2:
                continue
            g = gan_de[n]
            ratio = g / avg_c if avg_c < 500 else 0
            cycle_rows.append({
                "Số": n, "Lần ĐB": cnt,
                "Chu kỳ TB (ngày)": f"{avg_c:.0f}",
                "Độ đều": f"{cons:.2f}",
                "Gan hiện tại": g,
                "Gan/CK": f"{ratio:.2f}",
                "Chú ý": (
                    "🔥🔥 RẤT HOT" if ratio > 1.8
                    else "🔥 HOT"   if ratio > 1.2
                    else "⚠️ SẮP"   if ratio > 0.85
                    else ""
                ),
            })
        cycle_rows.sort(key=lambda r: -float(r["Gan/CK"]))
        st.table(cycle_rows)

    with st.expander("⑦ Số đề (GDB) hay về vào thứ nào?"):
        wd_freq_de = freq_by_weekday_de(h_raws, h_dates)
        wd_totals_de = {wd: sum(v.values()) for wd, v in wd_freq_de.items()}
        cols = st.columns(7)
        for wd in range(7):
            with cols[wd]:
                st.markdown(f"**{WD_NAMES[wd]}**")
                total = wd_totals_de[wd] or 1
                for num, c in sorted(wd_freq_de[wd].items(), key=lambda x: -x[1])[:5]:
                    st.write(f"{num}: {c} lần")

    st.info(
        "💡 **Cách dùng kết hợp:** Xem tab Quy luật để hiểu xu thế tổng thể, "
        "sau đó sang tab Lô Tô / Số Đề để lấy gợi ý cụ thể cho ngày mai."
    )

st.divider()
st.caption(
    "⚠️ Phân tích thống kê thuần túy – không đảm bảo kết quả. Chơi có trách nhiệm."
)

if __name__ == "__main__":
    s = {"G1": ["12345"], "GDB": ["99987"]}
    assert all_last2(s) == ["45", "87"]
    assert gdb_last2(s) == "87"
    print("Self-test OK.")
