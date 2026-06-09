import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import re
import json
from datetime import datetime

BASE_URL = "https://www.ptt.cc"
SEARCH_URL = "https://www.ptt.cc/bbs/Food/search?q=中壢"
HEADERS = {"User-Agent": "Mozilla/5.0"}
THIS_YEAR = datetime.now().year
GOOGLE_KEY = "AIzaSyCF6WZSkgcfhWQJ0DH4l3zUXXL8AkqZJtA"
BACKEND_URL = "http://localhost:8000/api/events/"

ZHONGLI_ROADS = [
    '環中東路', '環中東路一段', '環中東路二段', '環中東路三段',
    '中華路', '中華路一段', '中華路二段',
    '新生路', '民族路', '民生路', '忠孝路',
    '元化路', '復興路', '中央西路', '中央東路',
    '中正路', '延平路', '中山路', '中山東路',
    '中山東路一段', '中山東路二段', '中山東路三段',
    '新中北路', '新中北路一段', '新中北路二段',
    '中豐路', '環北路', '龍岡路', '龍岡路一段', '龍岡路二段',
    '南園一路', '南園二路', '南園三路',
    '中北路', '中北路一段', '中北路二段',
    '文化路', '文化路一段', '文化路二段',
    '介壽路', '建國路', '廣福路', '國際路',
    '國際路一段', '國際路二段',
    '永安路', '興豐路', '振興街',
    '中園路', '大仁街', '大仁路',
    '五權街', '協同街', '弘揚路',
    '仁海路', '仁愛路', '中美路',
    '勤學路', '莒光路', '長安路',
]

def get_article_content(link):
    try:
        res = requests.get(link, headers=HEADERS, timeout=10)
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")
        main_content = soup.select_one("#main-content")
        if not main_content:
            return ""
        for tag in main_content.select(".push"):
            tag.decompose()
        return main_content.get_text("\n").strip()
    except Exception as e:
        print("抓內文失敗：", e)
        return ""

def extract_address(content):
    patterns = [
        r"地址[:：]\s*(.+)",
        r"地點[:：]\s*(.+)",
        r"位置[:：]\s*(.+)",
        r"桃園市中壢[^\n]{2,30}",
        r"中壢市[^\n]{2,30}",
    ]
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            address = match.group(0) if '桃園市' in pattern or '中壢市' in pattern else match.group(1)
            return address.split("\n")[0].strip()[:50]
    return ""

def extract_road_name(address):
    for road in ZHONGLI_ROADS:
        if road in address:
            return road
    match = re.search(r'([\u4e00-\u9fa5]{2,8}(?:路|街|大道)(?:[一二三四五六七八九十]{0,2}段)?)', address)
    if match:
        return match.group(1)
    return ""

def extract_store_name(title, content):
    title = re.sub(r"\[食記\]|\[廣宣\]|\[請益\]|\[問題\]|\[心得\]", "", title)
    title = re.sub(r"桃園|中壢|推薦|分享", "", title).strip()
    if title:
        return title
    match = re.search(r"店名[:：]\s*(.+)", content)
    if match:
        return match.group(1).split("\n")[0].strip()
    return "未知店名"

def is_this_year(content):
    return str(THIS_YEAR) in content

def geocode_address(address):
    # 先取路名，只用路名搜尋成功率較高
    road = extract_road_name(address)
    queries = []
    if road:
        queries.append(f"桃園市中壢區{road}")
    # 去掉括號備註再試
    clean = address.split('（')[0].split('(')[0].strip()
    if clean != address:
        queries.append(clean)
    # 最後才試完整地址
    queries.append(address)

    for q in queries:
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": q, "format": "json", "limit": 1},
                headers={"User-Agent": "TrafficRiskPlatform/1.0"},
                timeout=10,
            )
            data = r.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"])
            time.sleep(1)
        except:
            pass
    return None, None
def upload_to_backend(events):
    success = 0
    for e in events:
        lat, lng = geocode_address(e["地址"])
        if not lat:
            print(f"  ✗ 無法取得座標：{e['地址']}")
            continue
        try:
            payload = {
                "type":        "other",
                "description": f"【PTT美食測試】{e['店名']} 位於 {e['地址']}（路名：{e['路名']}）",
                "lat":         lat,
                "lng":         lng,
                "confidence":  0.8,
                "source_url":  e["連結"],
                "crawled_at":  datetime.now().isoformat()
            }
            r = requests.post(BACKEND_URL, json=payload, timeout=10)
            if r.status_code in [200, 201]:
                success += 1
                print(f"  ✓ 上傳：{e['店名']} ({lat:.4f}, {lng:.4f})")
            else:
                print(f"  ✗ 上傳失敗 {r.status_code}")
        except Exception as ex:
            print(f"  ✗ 連線失敗：{ex}")
        time.sleep(2) 
    print(f"\n上傳完成：{success}/{len(events)} 筆")

def crawl_food_zhongli():
    res = requests.get(SEARCH_URL, headers=HEADERS, timeout=10)
    res.encoding = "utf-8"
    soup = BeautifulSoup(res.text, "html.parser")
    posts = soup.select(".r-ent")
    data = []

    for post in posts:
        title_tag = post.select_one(".title a")
        date_tag  = post.select_one(".date")
        if not title_tag:
            continue

        title = title_tag.text.strip()
        link  = BASE_URL + title_tag["href"]
        date  = date_tag.text.strip() if date_tag else ""

        if "中壢" not in title:
            continue

        content = get_article_content(link)
        if not is_this_year(content):
            continue

        address = extract_address(content)
        if not address:
            continue

        road = extract_road_name(address)
        store_name = extract_store_name(title, content)

        data.append({
            "來源": "PTT Food",
            "店名": store_name,
            "地區": "中壢",
            "地址": address,
            "路名": road,
            "標題": title,
            "日期": date,
            "連結": link
        })
        print(f"抓到：{store_name} | {road} | {address}")
        time.sleep(1)

    df = pd.DataFrame(data)
    df.to_csv("zhongli_food_address.csv", index=False, encoding="utf-8-sig")
    print(f"\n完成，共抓到 {len(data)} 筆")

    if data:
        print("\n上傳到後端...")
        upload_to_backend(data)

    return data

if __name__ == "__main__":
    print("爬蟲啟動，每60秒執行一次，按 Ctrl+C 停止")
    while True:
        try:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 開始爬取...")
            crawl_food_zhongli()
            print(f"等待60秒...")
            time.sleep(60)
        except KeyboardInterrupt:
            print("\n爬蟲已停止")
            break
        except Exception as e:
            print(f"發生錯誤：{e}，60秒後重試...")
            time.sleep(60)