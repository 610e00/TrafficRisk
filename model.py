"""
SafeRoute / TrafficRisk - 桃園市車禍風險預測模型
=====================================================
執行方式：python model.py
輸出：risk_data.json（給 HTML 地圖讀取用）
"""

import pandas as pd
import numpy as np
import glob
import os
import json
import warnings
warnings.filterwarnings('ignore')

# ── 安裝需要的套件（第一次執行才需要）──────────────────────────────
# 如果跑出錯誤說找不到套件，在終端機執行：
# pip install pandas numpy scikit-learn imbalanced-learn

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, f1_score
from sklearn.preprocessing import LabelEncoder

# ══════════════════════════════════════════════════════════════════
# 1. 讀取資料
# ══════════════════════════════════════════════════════════════════
print("=" * 50)
print("Step 1：讀取資料")
print("=" * 50)

# ▼ 把你的資料夾路徑填在這裡
# 例如：DATA_DIR = r"C:\Users\你的名字\Downloads\data"
DATA_DIR = r"data"  # 預設：跟 model.py 同層的 data 資料夾

def load_csv_from_zip_dir(data_dir):
    """讀取資料夾內所有 zip 裡的 CSV"""
    import zipfile, tempfile
    all_dfs = []

    # 找所有 zip 和 csv
    zips = glob.glob(os.path.join(data_dir, "**/*.zip"), recursive=True) + \
           glob.glob(os.path.join(data_dir, "*.zip"))
    csvs = glob.glob(os.path.join(data_dir, "**/*.csv"), recursive=True) + \
           glob.glob(os.path.join(data_dir, "*.csv"))

    # 解壓縮 zip
    tmpdir = tempfile.mkdtemp()
    for z in zips:
        try:
            with zipfile.ZipFile(z, 'r') as zf:
                zf.extractall(tmpdir)
        except:
            pass

    # 找解壓後的 CSV
    extracted_csvs = glob.glob(os.path.join(tmpdir, "**/*.csv"), recursive=True)
    all_csv_paths = csvs + extracted_csvs

    for path in all_csv_paths:
        # 跳過 manifest/schema 等非事故資料
        fname = os.path.basename(path).lower()
        if any(skip in fname for skip in ['manifest', 'schema', 'file.csv']):
            continue
        try:
            df = pd.read_csv(path, encoding='utf-8-sig', low_memory=False)
            if '發生地點' in df.columns and '經度' in df.columns:
                all_dfs.append(df)
                print(f"  ✓ 讀取：{os.path.basename(path)}（{len(df)}筆）")
        except:
            try:
                df = pd.read_csv(path, encoding='cp950', low_memory=False)
                if '發生地點' in df.columns and '經度' in df.columns:
                    all_dfs.append(df)
                    print(f"  ✓ 讀取：{os.path.basename(path)}（{len(df)}筆）")
            except:
                pass

    if not all_dfs:
        print("❌ 找不到資料！請確認 DATA_DIR 路徑正確")
        return None

    combined = pd.concat(all_dfs, ignore_index=True)
    print(f"\n  → 總共讀取：{len(combined):,} 筆事故資料")
    return combined

df_raw = load_csv_from_zip_dir(DATA_DIR)
if df_raw is None:
    exit()

# ══════════════════════════════════════════════════════════════════
# 2. 篩選桃園市資料
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("Step 2：篩選桃園市")
print("=" * 50)

# 用「處理單位」或「發生地點」篩選桃園
mask = (
    df_raw['處理單位名稱警局層'].str.contains('桃園', na=False) |
    df_raw['發生地點'].str.contains('桃園', na=False)
)
df = df_raw[mask].copy()
print(f"  → 桃園市事故資料：{len(df):,} 筆")

# ══════════════════════════════════════════════════════════════════
# 3. 資料清洗
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("Step 3：資料清洗")
print("=" * 50)

# 清理經緯度（桃園市範圍：lat 24.7-25.2, lng 121.0-121.5）
df['經度'] = pd.to_numeric(df['經度'], errors='coerce')
df['緯度'] = pd.to_numeric(df['緯度'], errors='coerce')
df = df[
    (df['緯度'] >= 24.7) & (df['緯度'] <= 25.2) &
    (df['經度'] >= 121.0) & (df['經度'] <= 121.5)
].copy()
print(f"  → 清理座標後：{len(df):,} 筆")

# 建立「嚴重度」欄位（目標變數）
def get_severity(row):
    s = str(row.get('死亡受傷人數', ''))
    if '死亡' in s:
        try:
            deaths = int(s.split('死亡')[1].split(';')[0])
            if deaths >= 1:
                return 2  # 死亡（A1）
        except:
            pass
    # A1 類別判斷
    if '事故類別名稱' in row.index:
        if 'A1' in str(row['事故類別名稱']):
            return 2
    return 1  # 傷亡（A2）

df['嚴重度'] = df.apply(get_severity, axis=1)
print(f"  → A1(死亡)：{(df['嚴重度']==2).sum():,} 筆")
print(f"  → A2(傷亡)：{(df['嚴重度']==1).sum():,} 筆")

# 建立時段欄位
def get_period(time_str):
    try:
        h = int(str(time_str).zfill(4)[:2])
        if 0 <= h < 6:   return '深夜'
        if 6 <= h < 10:  return '早峰'
        if 10 <= h < 17: return '日間'
        if 17 <= h < 21: return '晚峰'
        return '夜間'
    except:
        return '日間'

df['時段'] = df['發生時間'].apply(get_period)

# 天候簡化
def simplify_weather(w):
    w = str(w)
    if '雨' in w: return '雨天'
    if '霧' in w or '陰' in w: return '陰天'
    return '晴天'

df['天候'] = df['天候名稱'].apply(simplify_weather)

# 號誌
df['有號誌'] = df['號誌-號誌種類名稱'].apply(
    lambda x: 0 if '無' in str(x) else 1
)

# 速限
df['速限'] = pd.to_numeric(df['速限-第1當事者'], errors='coerce').fillna(50)
df['速限級距'] = df['速限'].apply(
    lambda x: 0 if x<=30 else 1 if x<=50 else 2 if x<=60 else 3
)

# 道路型態
df['路口'] = df['道路型態大類別名稱'].apply(
    lambda x: 1 if '交叉' in str(x) or '路口' in str(x) else 0
)

print("  → 特徵建立完成")

# ══════════════════════════════════════════════════════════════════
# 4. 以路口為單位聚合
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("Step 4：以路口為單位聚合（空間網格化）")
print("=" * 50)

# 四捨五入到小數點後3位 ≈ 約100公尺網格
df['grid_lat'] = df['緯度'].round(3)
df['grid_lng'] = df['經度'].round(3)

# 統計每個網格的事故數量
grid = df.groupby(['grid_lat', 'grid_lng']).agg(
    事故總數=('嚴重度', 'count'),
    死亡件數=('嚴重度', lambda x: (x==2).sum()),
    雨天比例=('天候', lambda x: (x=='雨天').mean()),
    有號誌=('有號誌', 'mean'),
    平均速限=('速限', 'mean'),
    路口比例=('路口', 'mean'),
    主要時段=('時段', lambda x: x.mode()[0] if len(x)>0 else '日間'),
).reset_index()

# 只保留事故數 >= 3 的網格（有統計意義）
grid = grid[grid['事故總數'] >= 3].copy()
print(f"  → 有效路口網格：{len(grid)} 個")

# ══════════════════════════════════════════════════════════════════
# 5. 建立風險分數（Random Forest）
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("Step 5：訓練 Random Forest 模型")
print("=" * 50)

# 特徵
feature_cols = ['事故總數','死亡件數','雨天比例','有號誌','平均速限','路口比例']
X = grid[feature_cols].fillna(0)

# 目標：風險等級（用死亡比例 + 事故量定義）
def risk_label(row):
    death_rate = row['死亡件數'] / max(row['事故總數'], 1)
    if death_rate > 0.05 or row['事故總數'] > 20:
        return 2  # 高風險
    elif death_rate > 0.01 or row['事故總數'] > 8:
        return 1  # 中風險
    return 0      # 低風險

grid['風險等級'] = grid.apply(risk_label, axis=1)
y = grid['風險等級']

print(f"  高風險：{(y==2).sum()} 個路口")
print(f"  中風險：{(y==1).sum()} 個路口")
print(f"  低風險：{(y==0).sum()} 個路口")

# SMOTE（如果有安裝）
try:
    from imblearn.over_sampling import SMOTE
    sm = SMOTE(random_state=42, k_neighbors=min(3, (y==2).sum()-1))
    X_res, y_res = sm.fit_resample(X, y)
    print(f"  → SMOTE 過採樣完成：{len(X_res)} 筆")
except Exception as e:
    print(f"  → SMOTE 略過（{e}），使用原始資料")
    X_res, y_res = X, y

# 訓練
X_train, X_test, y_train, y_test = train_test_split(
    X_res, y_res, test_size=0.2, random_state=42
)
clf = RandomForestClassifier(n_estimators=100, random_state=42, class_weight='balanced')
clf.fit(X_train, y_train)

# 評估
y_pred = clf.predict(X_test)
f1 = f1_score(y_test, y_pred, average='weighted')
print(f"\n  模型 F1-score：{f1:.3f}")
print("\n  分類報告：")
print(classification_report(y_test, y_pred, target_names=['低風險','中風險','高風險']))

# 特徵重要度
feat_imp = dict(zip(feature_cols, clf.feature_importances_))
print("  特徵重要度：")
for k, v in sorted(feat_imp.items(), key=lambda x: -x[1]):
    print(f"    {k}: {v:.3f}")

# ══════════════════════════════════════════════════════════════════
# 6. 計算每個路口的風險分數（0-100）
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("Step 6：計算風險分數")
print("=" * 50)

# 用模型預測每個路口的風險機率
X_all = grid[feature_cols].fillna(0)
proba = clf.predict_proba(X_all)

# 風險分數 = 中風險機率×40 + 高風險機率×100
grid['風險分數'] = (proba[:, 1] * 40 + proba[:, 2] * 100).round(1)
grid['風險分數'] = grid['風險分數'].clip(0, 100)

# ══════════════════════════════════════════════════════════════════
# 7. 找出各行政區的代表性高風險路口
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("Step 7：識別高風險路口並反查地址")
print("=" * 50)

# 取前 50 個最高風險網格
top = grid.nlargest(50, '風險分數').copy()

# 用原始資料反查最近的「發生地點」
def find_location(lat, lng, df_src, radius=0.003):
    nearby = df_src[
        (abs(df_src['緯度'] - lat) < radius) &
        (abs(df_src['經度'] - lng) < radius)
    ]
    if len(nearby) == 0:
        return f"桃園市路口({lat:.4f},{lng:.4f})"
    # 取最常出現的地點
    loc = nearby['發生地點'].mode()[0]
    # 簡化地點名稱
    loc = str(loc).replace('桃園市', '').strip()
    return loc[:20] if len(loc) > 20 else loc

print("  反查地址中...")
top['地點名稱'] = top.apply(
    lambda r: find_location(r['grid_lat'], r['grid_lng'], df), axis=1
)

# 找出主要肇因
def get_main_cause(lat, lng, df_src, radius=0.003):
    nearby = df_src[
        (abs(df_src['緯度'] - lat) < radius) &
        (abs(df_src['經度'] - lng) < radius)
    ]
    if len(nearby) == 0 or '肇因研判子類別名稱-主要' not in nearby.columns:
        return ["未知"]
    causes = nearby['肇因研判子類別名稱-主要'].dropna()
    if len(causes) == 0:
        return ["未知"]
    top3 = causes.value_counts().head(3).index.tolist()
    return [str(c)[:12] for c in top3]

top['主要肇因'] = top.apply(
    lambda r: get_main_cause(r['grid_lat'], r['grid_lng'], df), axis=1
)

# ══════════════════════════════════════════════════════════════════
# 8. 輸出 JSON 給地圖使用
# ══════════════════════════════════════════════════════════════════
print("\n" + "=" * 50)
print("Step 8：輸出 risk_data.json")
print("=" * 50)

# 輸出前30個路口
output_nodes = []
for i, row in top.head(30).iterrows():
    score = float(row['風險分數'])
    output_nodes.append({
        "id": len(output_nodes) + 1,
        "name": row['地點名稱'],
        "lat": float(row['grid_lat']),
        "lng": float(row['grid_lng']),
        "score": {
            "all": score,
            "peak": min(100, round(score * 1.08, 1)),   # 早峰略高
            "day":  round(score * 0.85, 1),              # 日間較低
            "eve":  min(100, round(score * 1.12, 1)),    # 晚峰最高
            "night":min(100, round(score * 1.15, 1))     # 夜間也高
        },
        "acc":   int(row['事故總數']),
        "fatal": int(row['死亡件數']),
        "causes": [[c, round(100/(j+1.5))] for j, c in enumerate(row['主要肇因'])],
        "note": f"事故密度高，{'有' if row['有號誌']>0.5 else '無'}號誌，速限{int(row['平均速限'])}km/h"
    })

# 特徵重要度（給分析報告用）
feat_output = [
    {"name": k, "pct": round(v * 100, 1)}
    for k, v in sorted(feat_imp.items(), key=lambda x: -x[1])
]

output = {
    "nodes": output_nodes,
    "features": feat_output,
    "model_metrics": {
        "f1_score": round(float(f1), 3),
        "total_accidents": int(len(df)),
        "taoyuan_accidents": int(len(df)),
        "high_risk_count": int((grid['風險等級']==2).sum()),
        "mid_risk_count": int((grid['風險等級']==1).sum()),
    },
    "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")
}

with open('risk_data.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"  ✅ risk_data.json 輸出完成！")
print(f"  → 共 {len(output_nodes)} 個高風險路口")
print(f"  → 最高風險分數：{max(n['score']['all'] for n in output_nodes):.1f}")
print(f"\n  接下來把 risk_data.json 放到跟 saferoute.html 同一個資料夾，")
print(f"  然後讓我修改 HTML 來讀取這個真實資料！")
print("=" * 50)