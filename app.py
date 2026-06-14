import base64
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List
from uuid import uuid4

import streamlit as st


APP_TITLE = "AI 스마트 분리수거 & 쓰레기 뉴스"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "app_data"
IMAGE_DIR = DATA_DIR / "history_images"
HISTORY_FILE = DATA_DIR / "waste_history.json"
NEWS_FILE = DATA_DIR / "news_cache.json"
NAVER_NEWS_FILE = DATA_DIR / "naver_news_cache.json"

MAX_HISTORY = 50
MAX_NEWS = 6

DEFAULT_NEWS = [
    {
        "published": "예시",
        "title": "분리배출 전 내용물 비우기와 세척이 중요합니다",
        "description": "재활용품은 내용물을 비우고 이물질을 제거한 뒤 재질별로 분리해야 합니다.",
        "source": "기본 안내",
        "source_url": "",
        "link": "",
        "summary": "재활용품은 비우고, 헹구고, 재질별로 나누어 배출해야 합니다.",
        "summary_source": "기본 요약",
    },
    {
        "published": "예시",
        "title": "오염된 종이와 플라스틱은 재활용이 어려울 수 있습니다",
        "description": "음식물과 기름이 심하게 묻은 포장재는 지역 기준에 따라 일반쓰레기로 분류될 수 있습니다.",
        "source": "기본 안내",
        "source_url": "",
        "link": "",
        "summary": "음식물이나 기름이 심하게 묻은 포장재는 재활용이 어려울 수 있습니다.",
        "summary_source": "기본 요약",
    },
]

REQUIRED_RESULT_KEYS = [
    "품목명",
    "쓰레기 종류",
    "배출 방법",
    "자취생 꿀팁",
    "관련 법안 한 줄 요약",
    "주의사항",
]


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except Exception:
        return default


def write_json(path: Path, data: Any) -> None:
    ensure_storage()
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def get_secret_or_env(name: str) -> str:
    if os.getenv(name):
        return os.getenv(name, "")
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return ""


def safe_json_from_text(raw_text: str) -> Any:
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        object_match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if object_match:
            return json.loads(object_match.group(0))
        array_match = re.search(r"\[.*\]", cleaned, flags=re.DOTALL)
        if array_match:
            return json.loads(array_match.group(0))
        raise


def create_gemini_client(api_key: str):
    if not api_key.strip():
        raise ValueError("Gemini API 키를 입력해 주세요.")
    from google import genai
    return genai.Client(api_key=api_key.strip())


def call_gemini_text(prompt: str, api_key: str, model_name: str) -> str:
    client = create_gemini_client(api_key)
    response = client.models.generate_content(
        model=model_name.strip(),
        contents=prompt,
    )
    return response.text or ""


def call_gemini_image(
    prompt: str,
    image_bytes: bytes,
    mime_type: str,
    api_key: str,
    model_name: str,
) -> str:
    from google.genai import types

    client = create_gemini_client(api_key)
    image_part = types.Part.from_bytes(
        data=image_bytes,
        mime_type=mime_type or "image/jpeg",
    )
    response = client.models.generate_content(
        model=model_name.strip(),
        contents=[image_part, prompt],
    )
    return response.text or ""


def create_openai_client(api_key: str):
    if not api_key.strip():
        raise ValueError("OpenAI API 키를 입력해 주세요.")

    from openai import OpenAI

    return OpenAI(api_key=api_key.strip())


def call_openai_text(prompt: str, api_key: str, model_name: str) -> str:
    client = create_openai_client(api_key)
    response = client.responses.create(
        model=model_name.strip(),
        input=prompt,
    )
    return response.output_text or ""


def call_openai_image(
    prompt: str,
    image_bytes: bytes,
    mime_type: str,
    api_key: str,
    model_name: str,
) -> str:
    client = create_openai_client(api_key)
    encoded_image = base64.b64encode(image_bytes).decode("ascii")
    image_data_url = (
        f"data:{mime_type or 'image/jpeg'};base64,{encoded_image}"
    )

    response = client.responses.create(
        model=model_name.strip(),
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {
                        "type": "input_image",
                        "image_url": image_data_url,
                        "detail": "auto",
                    },
                ],
            }
        ],
    )
    return response.output_text or ""


def call_ai_text(
    provider: str,
    prompt: str,
    api_key: str,
    model_name: str,
) -> str:
    if provider == "OpenAI (GPT)":
        return call_openai_text(prompt, api_key, model_name)
    return call_gemini_text(prompt, api_key, model_name)


def call_ai_image(
    provider: str,
    prompt: str,
    image_bytes: bytes,
    mime_type: str,
    api_key: str,
    model_name: str,
) -> str:
    if provider == "OpenAI (GPT)":
        return call_openai_image(
            prompt=prompt,
            image_bytes=image_bytes,
            mime_type=mime_type,
            api_key=api_key,
            model_name=model_name,
        )

    return call_gemini_image(
        prompt=prompt,
        image_bytes=image_bytes,
        mime_type=mime_type,
        api_key=api_key,
        model_name=model_name,
    )


def build_analysis_prompt(item_hint: str) -> str:
    return f"""
당신은 대한민국 생활폐기물 분리배출 안내 전문가입니다.
사용자가 제공한 사진과 품목 힌트를 바탕으로 쓰레기를 분석하세요.

품목 힌트: {item_hint or "없음"}

아래 조건을 반드시 지키세요.
- 사진에서 보이는 재질과 오염 상태를 함께 판단하세요.
- 지역별 분리배출 기준이 다를 수 있다는 점을 반영하세요.
- 확실하지 않은 법률 조항 번호나 과태료 금액을 지어내지 마세요.
- 법안 항목은 대한민국의 일반적인 분리배출·폐기물 관리 원칙을 한 문장으로 요약하세요.
- 답변은 설명문 없이 JSON 객체 하나만 출력하세요.

반드시 포함할 키:
{json.dumps(REQUIRED_RESULT_KEYS, ensure_ascii=False)}
""".strip()


def normalize_result(result: Dict[str, Any]) -> Dict[str, str]:
    normalized: Dict[str, str] = {}
    for key in REQUIRED_RESULT_KEYS:
        value = result.get(key, "정보 없음")
        normalized[key] = str(value).strip() or "정보 없음"
    return normalized


def mock_analysis(item_hint: str, has_image: bool) -> Dict[str, str]:
    name = (item_hint or "").strip()

    if "페트" in name or "생수" in name:
        result = {
            "품목명": "투명 페트병",
            "쓰레기 종류": "플라스틱류",
            "배출 방법": "내용물을 비우고 라벨을 제거한 뒤 압착하여 투명 페트병 수거함에 배출하세요.",
            "자취생 꿀팁": "병 안을 한 번 헹구고 부피를 줄이면 보관 공간을 아낄 수 있습니다.",
            "관련 법안 한 줄 요약": "재활용품은 내용물을 비우고 이물질을 제거해 재질별로 분리배출하는 것이 기본 원칙입니다.",
            "주의사항": "뚜껑 처리 방식은 지자체별로 다를 수 있으니 지역 안내를 확인하세요.",
        }
    elif "상자" in name or "박스" in name:
        result = {
            "품목명": "종이 상자",
            "쓰레기 종류": "종이류",
            "배출 방법": "테이프와 송장 등 다른 재질을 제거하고 상자를 펼쳐 종이류로 배출하세요.",
            "자취생 꿀팁": "오염된 부분만 잘라내면 깨끗한 부분은 종이류로 분리할 수 있습니다.",
            "관련 법안 한 줄 요약": "재활용 가능한 종이는 비닐과 테이프 등 이물질을 제거한 뒤 분리배출해야 합니다.",
            "주의사항": "음식물과 기름이 심하게 묻은 종이는 일반쓰레기로 분류될 수 있습니다.",
        }
    elif "배달" in name or "용기" in name:
        result = {
            "품목명": "배달 음식 용기",
            "쓰레기 종류": "플라스틱류 또는 일반쓰레기",
            "배출 방법": "음식물을 비우고 씻은 뒤 깨끗하면 플라스틱류로, 오염이 제거되지 않으면 일반쓰레기로 배출하세요.",
            "자취생 꿀팁": "키친타월로 기름을 먼저 닦은 뒤 세척하면 물 사용량과 냄새를 줄일 수 있습니다.",
            "관련 법안 한 줄 요약": "오염된 재활용품은 선별을 방해하므로 이물질을 제거한 뒤 배출해야 합니다.",
            "주의사항": "검은색 플라스틱과 복합재질은 지역 선별 기준에 따라 재활용이 제한될 수 있습니다.",
        }
    else:
        result = {
            "품목명": name or ("사진 속 쓰레기" if has_image else "미확인 품목"),
            "쓰레기 종류": "테스트 모드 분석 결과",
            "배출 방법": "내용물을 비우고 세척한 뒤 재질 표시를 확인하여 지역 분리배출 기준에 맞게 배출하세요.",
            "자취생 꿀팁": "싱크대 옆에 작은 세척용 솔을 두면 배달 용기와 병을 빠르게 씻을 수 있습니다.",
            "관련 법안 한 줄 요약": "생활폐기물은 지자체가 정한 방법과 장소에 맞게 분리배출해야 합니다.",
            "주의사항": "현재는 테스트 모드이므로 실제 사진 판독 결과가 아닙니다.",
        }
    return normalize_result(result)


def analyze_waste(
    image_bytes: bytes,
    mime_type: str,
    item_hint: str,
    provider: str,
    api_key: str,
    model_name: str,
    test_mode: bool,
) -> Dict[str, str]:
    if test_mode:
        return mock_analysis(item_hint, bool(image_bytes))

    raw_text = call_ai_image(
        provider=provider,
        prompt=build_analysis_prompt(item_hint),
        image_bytes=image_bytes,
        mime_type=mime_type,
        api_key=api_key,
        model_name=model_name,
    )
    parsed = safe_json_from_text(raw_text)
    if not isinstance(parsed, dict):
        raise ValueError("AI가 JSON 객체 형식으로 답하지 않았습니다.")
    return normalize_result(parsed)


def save_history_image(image_bytes: bytes, mime_type: str) -> str:
    ensure_storage()
    extension_map = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }
    extension = extension_map.get(mime_type, ".jpg")
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}{extension}"
    path = IMAGE_DIR / filename
    path.write_bytes(image_bytes)
    return str(path.relative_to(BASE_DIR))


def add_history(result: Dict[str, str], image_bytes: bytes, mime_type: str) -> None:
    history = read_json(HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []
    image_path = save_history_image(image_bytes, mime_type)
    history.insert(
        0,
        {
            "id": uuid4().hex,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "image_path": image_path,
            "result": result,
        },
    )
    write_json(HISTORY_FILE, history[:MAX_HISTORY])


def delete_all_history() -> None:
    history = read_json(HISTORY_FILE, [])
    if isinstance(history, list):
        for record in history:
            image_path = record.get("image_path")
            if image_path:
                full_path = BASE_DIR / image_path
                try:
                    if full_path.exists():
                        full_path.unlink()
                except Exception:
                    pass
    write_json(HISTORY_FILE, [])


def get_site_domain(url: str) -> str:
    if not url:
        return ""
    try:
        hostname = urllib.parse.urlparse(url).hostname or ""
        return hostname.removeprefix("www.")
    except Exception:
        return ""


def basic_news_summary(title: str, description: str) -> str:
    text = strip_html(description)
    if text:
        first_sentence = re.split(r"(?<=[.!?다요])\s+", text)[0].strip()
        return first_sentence[:110] + ("…" if len(first_sentence) > 110 else "")
    clean_title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
    return clean_title[:110] + ("…" if len(clean_title) > 110 else "")


def fetch_google_news(query: str, max_items: int = MAX_NEWS) -> List[Dict[str, str]]:
    encoded = urllib.parse.quote(query)
    url = "https://news.google.com/rss/search?" f"q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 SmartWasteNews/1.0"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)
    items: List[Dict[str, str]] = []
    for item in root.findall(".//item")[:max_items]:
        title = strip_html(item.findtext("title", "제목 없음"))
        description = strip_html(item.findtext("description", ""))
        link = item.findtext("link", "")
        source_node = item.find("source")
        source = strip_html(
            source_node.text if source_node is not None and source_node.text else "Google News"
        )
        source_url = source_node.attrib.get("url", "") if source_node is not None else ""
        pub_date = item.findtext("pubDate", "")
        try:
            published = parsedate_to_datetime(pub_date).strftime("%Y.%m.%d %H:%M")
        except Exception:
            published = datetime.now().strftime("%Y.%m.%d %H:%M")
        items.append(
            {
                "published": published,
                "title": title,
                "description": description[:500],
                "source": source,
                "source_url": source_url,
                "link": link,
                "summary": basic_news_summary(title, description),
                "summary_source": "기본 요약",
            }
        )
    return items


def fetch_naver_news(
    query: str,
    client_id: str,
    client_secret: str,
    max_items: int = MAX_NEWS,
) -> List[Dict[str, str]]:
    if not client_id.strip() or not client_secret.strip():
        raise ValueError("Naver Client ID와 Client Secret이 필요합니다.")

    params = urllib.parse.urlencode(
        {
            "query": query,
            "display": max_items,
            "start": 1,
            "sort": "date",
        }
    )
    url = f"https://openapi.naver.com/v1/search/news.json?{params}"
    request = urllib.request.Request(
        url,
        headers={
            "X-Naver-Client-Id": client_id.strip(),
            "X-Naver-Client-Secret": client_secret.strip(),
            "User-Agent": "Mozilla/5.0 SmartWasteNews/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))

    items: List[Dict[str, str]] = []
    for item in payload.get("items", [])[:max_items]:
        title = strip_html(item.get("title", "제목 없음"))
        description = strip_html(item.get("description", ""))
        link = item.get("originallink") or item.get("link", "")
        pub_date = item.get("pubDate", "")
        try:
            published = parsedate_to_datetime(pub_date).strftime("%Y.%m.%d %H:%M")
        except Exception:
            published = datetime.now().strftime("%Y.%m.%d %H:%M")
        items.append(
            {
                "published": published,
                "title": title,
                "description": description[:500],
                "source": "Naver News",
                "source_url": "",
                "link": link,
                "summary": basic_news_summary(title, description),
                "summary_source": "기본 요약",
            }
        )
    return items


def summarize_news_with_ai(
    news_items: List[Dict[str, str]],
    provider: str,
    api_key: str,
    model_name: str,
) -> List[Dict[str, str]]:
    if not news_items:
        return news_items

    article_payload = [
        {
            "id": index,
            "title": item["title"],
            "description": item.get("description", ""),
        }
        for index, item in enumerate(news_items)
    ]
    prompt = f"""
아래는 쓰레기 배출, 재활용, 폐기물 정책과 관련된 한국어 뉴스 목록입니다.
각 기사를 45자 안팎의 쉬운 한국어 한 문장으로 요약하세요.
기사에 없는 사실을 추가하지 마세요.
답변은 설명 없이 JSON 배열 하나만 출력하세요.

출력 형식:
[{{"id": 0, "summary": "한 줄 요약"}}]

기사:
{json.dumps(article_payload, ensure_ascii=False)}
""".strip()
    raw_text = call_ai_text(provider, prompt, api_key, model_name)
    parsed = safe_json_from_text(raw_text)
    if not isinstance(parsed, list):
        raise ValueError("뉴스 요약 결과가 JSON 배열이 아닙니다.")

    summary_map: Dict[int, str] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id"))
        except Exception:
            continue
        summary = str(item.get("summary", "")).strip()
        if summary:
            summary_map[item_id] = summary

    updated: List[Dict[str, str]] = []
    for index, item in enumerate(news_items):
        copied = dict(item)
        if index in summary_map:
            copied["summary"] = summary_map[index]
            copied["summary_source"] = (
                "GPT AI 요약"
                if provider == "OpenAI (GPT)"
                else "Gemini AI 요약"
            )
        updated.append(copied)
    return updated


def refresh_news(
    query: str,
    use_ai_summary: bool,
    provider: str,
    api_key: str,
    model_name: str,
    source: str = "google",
    client_id: str = "",
    client_secret: str = "",
    cache_path: Path = NEWS_FILE,
) -> None:
    if source == "naver":
        items = fetch_naver_news(query, client_id, client_secret)
    else:
        items = fetch_google_news(query)

    if use_ai_summary and api_key.strip():
        items = summarize_news_with_ai(
            news_items=items,
            provider=provider,
            api_key=api_key,
            model_name=model_name,
        )
    write_json(
        cache_path,
        {
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "query": query,
            "source": source,
            "items": items,
        },
    )


def load_news(cache_path: Path = NEWS_FILE) -> Dict[str, Any]:
    cached = read_json(cache_path, {})
    if isinstance(cached, dict) and isinstance(cached.get("items"), list):
        return cached
    return {"updated_at": "저장된 뉴스 없음", "query": "", "items": DEFAULT_NEWS}


def image_path_to_data_uri(path: Path) -> str:
    try:
        suffix = path.suffix.lower()
        mime = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(suffix, "image/jpeg")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{encoded}"
    except Exception:
        return ""


def inject_style() -> None:
    st.markdown(
        """
<style>
:root {
  --bg:#f5f7f4; --panel:#ffffff; --ink:#17211a; --muted:#657168;
  --line:#dfe6df; --green:#197a45; --green-dark:#105d34;
  --green-soft:#eaf6ee; --yellow-soft:#fff8de;
  --shadow:0 12px 30px rgba(21,55,33,.08); --radius:18px;
}
html, body, [class*="css"] { font-family: Pretendard, "Noto Sans KR", Arial, sans-serif; }
.stApp { background:var(--bg); color:var(--ink); }
.block-container { max-width:1500px; padding:1.8rem 2rem 4rem; }
header[data-testid="stHeader"] { background:transparent; }
[data-testid="stToolbar"] { right:1rem; }

/* Sidebar */
section[data-testid="stSidebar"] {
  width:290px !important;
  background:radial-gradient(circle at 20% 0%, rgba(255,255,255,.16), transparent 28%), linear-gradient(160deg,#153f2a,#0d2e1e);
  border-right:0;
}
section[data-testid="stSidebar"] > div { padding-top:1rem; }
section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span { color:#f4fff7 !important; }
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p { color:#d2e3d7 !important; font-size:.79rem; }
section[data-testid="stSidebar"] div[data-baseweb="input"] > div,
section[data-testid="stSidebar"] div[data-baseweb="base-input"] {
  background:rgba(255,255,255,.08) !important;
  border-color:rgba(255,255,255,.18) !important;
  border-radius:11px !important;
}
section[data-testid="stSidebar"] input { color:white !important; -webkit-text-fill-color:white !important; }
section[data-testid="stSidebar"] input::placeholder { color:rgba(255,255,255,.55) !important; }
section[data-testid="stSidebar"] hr { border-color:rgba(255,255,255,.13); }
section[data-testid="stSidebar"] [data-testid="stAlert"] {
  border:1px solid rgba(255,255,255,.12); background:rgba(255,255,255,.08);
}
section[data-testid="stSidebar"] [data-testid="stAlert"] * { color:#eaf7ed !important; }
button[kind="header"] {
  color:var(--green-dark) !important;
  background:rgba(25,122,69,.10) !important;
  border-radius:10px !important;
}
button[kind="header"] svg { color:var(--green-dark) !important; fill:var(--green-dark) !important; }
section[data-testid="stSidebar"] button[kind="header"] {
  color:#ffffff !important;
  background:rgba(255,255,255,.16) !important;
}
section[data-testid="stSidebar"] button[kind="header"] svg { color:#ffffff !important; fill:#ffffff !important; }

/* Main widgets */
div[data-testid="stVerticalBlockBorderWrapper"] {
  border:1px solid var(--line) !important; border-radius:var(--radius) !important;
  background:var(--panel) !important; box-shadow:var(--shadow); padding:2px;
}
.stButton > button, .stLinkButton > a {
  border:0 !important; border-radius:11px !important; min-height:2.65rem;
  font-weight:700 !important; transition:.18s; box-shadow:none !important;
}
.stButton > button:hover, .stLinkButton > a:hover { transform:translateY(-1px); }
.stButton > button[kind="primary"] { background:var(--green) !important; color:white !important; }
.stButton > button[kind="primary"]:hover { background:var(--green-dark) !important; }
.stButton > button:not([kind="primary"]), .stLinkButton > a { background:#edf2ed !important; color:var(--ink) !important; }
[data-testid="stFileUploaderDropzone"] {
  min-height:200px; display:flex; align-items:center; border:2px dashed #b9cabb !important;
  border-radius:16px !important; background:#f8fbf8 !important;
}
[data-testid="stFileUploaderDropzone"] button { background:#edf2ed !important; color:var(--ink) !important; border:0 !important; }
[data-testid="stCameraInput"] { border:2px dashed #b9cabb; border-radius:16px; padding:12px; background:#f8fbf8; }
[data-baseweb="tab-list"] { gap:.35rem; }
[data-baseweb="tab"] { border-radius:10px 10px 0 0; }
[data-baseweb="tab"][aria-selected="true"] { color:var(--green-dark); font-weight:800; }
.stTextInput input { border-radius:11px !important; }

/* Custom HTML */
.hero-wrap { display:flex; justify-content:space-between; align-items:flex-end; gap:20px; margin:0 0 24px; }
.hero-title { margin:0 0 8px; font-size:clamp(28px,3vw,42px); line-height:1.16; letter-spacing:-1.4px; font-weight:850; }
.hero-desc { margin:0; color:var(--muted); font-size:1rem; }
.mode-badge { display:inline-flex; align-items:center; gap:7px; padding:9px 12px; border-radius:999px; color:var(--green-dark); font-size:13px; font-weight:800; white-space:nowrap; background:var(--green-soft); border:1px solid #cce9d5; }
.section-heading { display:flex; align-items:center; gap:8px; margin:0; font-size:1.2rem; font-weight:850; }
.mini-caption { margin:.1rem 0 1rem; color:var(--muted); font-size:.77rem; }
.news-card { padding:15px; margin:0 0 13px; border-radius:14px; border:1px solid var(--line); background:#fbfcfb; }
.summary-chip { display:inline-block; margin-bottom:9px; padding:5px 8px; border-radius:8px; color:#704c00; font-size:11px; font-weight:850; background:var(--yellow-soft); }
.news-summary { margin:0 0 10px; color:#36433a; font-size:13px; line-height:1.58; }
.news-title { margin:0 0 8px; font-size:15px; line-height:1.5; font-weight:850; }
.news-meta { color:var(--muted); font-size:11px; line-height:1.45; }
.news-actions { display:flex; gap:8px; margin-top:12px; }
.news-actions a { flex:1; text-align:center; text-decoration:none; padding:10px 11px; border-radius:10px; background:#edf2ed; color:var(--ink) !important; font-size:12px; font-weight:800; }
.result-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
.result-card { padding:14px; border:1px solid var(--line); border-radius:13px; background:white; }
.result-card strong { display:block; margin-bottom:6px; color:var(--green-dark); font-size:13px; }
.result-card p { margin:0; line-height:1.58; font-size:14px; }
.history-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(185px,1fr)); gap:12px; }
.history-card { overflow:hidden; border-radius:14px; border:1px solid var(--line); background:white; }
.history-card img { width:100%; height:125px; display:block; object-fit:cover; background:#edf2ed; }
.history-body { padding:11px; }
.history-body h4 { margin:0 0 5px; font-size:14px; }
.history-body p { margin:0 0 4px; font-size:11px; color:var(--muted); }
.history-body details { margin-top:8px; font-size:12px; color:#36433a; }
.empty-card { padding:24px; text-align:center; color:var(--muted); border:1px dashed var(--line); border-radius:14px; background:#fbfcfb; }
.sidebar-brand { display:flex; gap:12px; align-items:center; margin:.2rem 0 1.2rem; }
.sidebar-icon { width:46px; height:46px; display:grid; place-items:center; border-radius:15px; font-size:24px; background:rgba(255,255,255,.13); border:1px solid rgba(255,255,255,.15); }
.sidebar-brand-text { color:white; font-size:18px; font-weight:850; line-height:1.35; }
.sidebar-rule { height:1px; background:rgba(255,255,255,.13); margin:1rem 0; }
@media(max-width:760px){ .block-container{padding:1rem 1rem 3rem}.hero-wrap{align-items:flex-start;flex-direction:column}.result-grid{grid-template-columns:1fr}.news-actions{flex-direction:column} }
</style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> Dict[str, Any]:
    st.sidebar.markdown(
        """
<div class="sidebar-brand">
  <div class="sidebar-icon">♻️</div>
  <div class="sidebar-brand-text">AI 스마트 분리수거<br>쓰레기 뉴스</div>
</div>
<div class="sidebar-rule"></div>
        """,
        unsafe_allow_html=True,
    )

    st.sidebar.markdown("### AI 연결")
    provider = st.sidebar.selectbox(
        "AI 제공자",
        ["Gemini", "OpenAI (GPT)"],
        help="사진 분석과 뉴스 AI 요약에 사용할 서비스를 선택합니다.",
    )
    test_mode = st.sidebar.toggle(
        "테스트 모드 사용",
        value=True,
        help="켜져 있으면 API를 호출하지 않고 예시 결과를 보여줍니다.",
    )

    if provider == "OpenAI (GPT)":
        api_key = st.sidebar.text_input(
            "OpenAI API 키",
            type="password",
            placeholder="사용자 본인의 OpenAI API 키를 입력하세요",
            help="입력한 키는 파일에 저장되지 않습니다.",
            key="openai_api_key",
        )
        model_name = st.sidebar.text_input(
            "GPT 모델명",
            value="gpt-4.1-mini",
            key="openai_model_name",
        )
    else:
        api_key = st.sidebar.text_input(
            "Gemini API 키",
            type="password",
            placeholder="사용자 본인의 Gemini API 키를 입력하세요",
            help="입력한 키는 파일에 저장되지 않습니다.",
            key="gemini_api_key",
        )
        model_name = st.sidebar.text_input(
            "Gemini 모델명",
            value="gemini-2.5-flash",
            key="gemini_model_name",
        )

    if test_mode:
        st.sidebar.info("테스트 모드에서는 API 비용이 발생하지 않습니다.")
    elif api_key:
        st.sidebar.success(f"{provider} API 키가 입력되었습니다.")
    else:
        st.sidebar.warning(f"실제 AI 분석을 하려면 {provider} API 키가 필요합니다.")

    st.sidebar.markdown('<div class="sidebar-rule"></div>', unsafe_allow_html=True)
    st.sidebar.markdown("### 뉴스 설정")
    news_query = st.sidebar.text_input(
        "뉴스 검색어",
        value="분리수거 재활용 폐기물 배출 과태료",
    )
    use_ai_news_summary = st.sidebar.checkbox(
        "새로고침할 때 AI 한 줄 요약 생성",
        value=True,
        help="테스트 모드가 꺼져 있고 선택한 AI의 API 키가 있을 때 기사별 한 줄 요약을 만듭니다.",
    )

    naver_client_id = get_secret_or_env("NAVER_CLIENT_ID")
    naver_client_secret = get_secret_or_env("NAVER_CLIENT_SECRET")
    if naver_client_id and naver_client_secret:
        st.sidebar.success("Naver 검색 API 키가 Secrets에 설정되어 있습니다.")
    else:
        st.sidebar.warning("Naver 뉴스 탭을 쓰려면 Streamlit Secrets에 Naver API 키를 설정하세요.")
        naver_client_id = st.sidebar.text_input(
            "Naver Client ID",
            value=naver_client_id,
            type="password",
            help="로컬 테스트용 입력칸입니다. GitHub 코드에는 저장되지 않습니다.",
        )
        naver_client_secret = st.sidebar.text_input(
            "Naver Client Secret",
            value=naver_client_secret,
            type="password",
            help="로컬 테스트용 입력칸입니다. GitHub 코드에는 저장되지 않습니다.",
        )

    st.sidebar.info(
        "Google 뉴스 수집에는 API 키가 필요하지 않습니다. Naver 뉴스 수집에는 Naver 개발자센터 키가 필요합니다. "
        "선택한 AI의 API 키는 사진 분석과 AI 뉴스 요약에만 사용됩니다."
    )
    st.sidebar.caption(
        "API 키는 현재 실행 세션에서만 사용되며 app.py나 기록 파일에 저장되지 않습니다."
    )

    return {
        "provider": provider,
        "test_mode": test_mode,
        "api_key": api_key,
        "model_name": model_name,
        "news_query": news_query,
        "use_ai_news_summary": use_ai_news_summary,
        "naver_client_id": naver_client_id,
        "naver_client_secret": naver_client_secret,
    }


def news_cards_html(items: List[Dict[str, str]]) -> str:
    cards: List[str] = []
    for item in items:
        title = html.escape(item.get("title", "제목 없음"))
        summary = html.escape(item.get("summary", "요약 없음"))
        summary_source = html.escape(item.get("summary_source", "요약"))
        source = html.escape(item.get("source", "출처 없음"))
        published = html.escape(item.get("published", ""))
        source_url = item.get("source_url", "")
        article_url = item.get("link", "")
        domain = html.escape(get_site_domain(source_url))

        actions: List[str] = []
        if article_url:
            actions.append(
                f'<a href="{html.escape(article_url, quote=True)}" target="_blank" rel="noopener noreferrer">기사 원문 보기</a>'
            )
        if source_url:
            actions.append(
                f'<a href="{html.escape(source_url, quote=True)}" target="_blank" rel="noopener noreferrer">언론사 사이트</a>'
            )
        action_html = f'<div class="news-actions">{"".join(actions)}</div>' if actions else ""
        site_text = f" · {domain}" if domain else ""
        cards.append(
            f"""
<div class="news-card">
  <span class="summary-chip">🤖 {summary_source}</span>
  <p class="news-summary">{summary}</p>
  <h3 class="news-title">{title}</h3>
  <div class="news-meta">{published} · {source}{site_text}</div>
  {action_html}
</div>
            """
        )
    return "".join(cards)


def render_news_panel(settings: Dict[str, Any]) -> None:
    if not NEWS_FILE.exists():
        try:
            refresh_news(
                query=str(settings["news_query"]),
                use_ai_summary=False,
                provider=str(settings["provider"]),
                api_key="",
                model_name=str(settings["model_name"]),
                source="google",
                cache_path=NEWS_FILE,
            )
        except Exception:
            pass
    if not NAVER_NEWS_FILE.exists() and settings.get("naver_client_id") and settings.get("naver_client_secret"):
        try:
            refresh_news(
                query=str(settings["news_query"]),
                use_ai_summary=False,
                provider=str(settings["provider"]),
                api_key="",
                model_name=str(settings["model_name"]),
                source="naver",
                client_id=str(settings["naver_client_id"]),
                client_secret=str(settings["naver_client_secret"]),
                cache_path=NAVER_NEWS_FILE,
            )
        except Exception:
            pass

    with st.container(border=True):
        head_left, head_right = st.columns([2.2, 1])
        with head_left:
            st.markdown('<div class="section-heading">📰 최근 쓰레기·분리수거 뉴스</div>', unsafe_allow_html=True)
        with head_right:
            refresh_clicked = st.button("🔄 뉴스 새로고침", use_container_width=True)

        if refresh_clicked:
            with st.spinner("최신 뉴스를 불러오는 중입니다..."):
                try:
                    use_ai = (
                        bool(settings["use_ai_news_summary"])
                        and not bool(settings["test_mode"])
                        and bool(str(settings["api_key"]).strip())
                    )
                    refresh_news(
                        query=str(settings["news_query"]),
                        use_ai_summary=use_ai,
                        provider=str(settings["provider"]),
                        api_key=str(settings["api_key"]),
                        model_name=str(settings["model_name"]),
                        source="google",
                        cache_path=NEWS_FILE,
                    )
                    if settings.get("naver_client_id") and settings.get("naver_client_secret"):
                        refresh_news(
                            query=str(settings["news_query"]),
                            use_ai_summary=use_ai,
                            provider=str(settings["provider"]),
                            api_key=str(settings["api_key"]),
                            model_name=str(settings["model_name"]),
                            source="naver",
                            client_id=str(settings["naver_client_id"]),
                            client_secret=str(settings["naver_client_secret"]),
                            cache_path=NAVER_NEWS_FILE,
                        )
                    st.success("최신 뉴스로 갱신했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"뉴스 새로고침에 실패했습니다: {exc}")

        google_tab, naver_tab = st.tabs(["Google 뉴스", "Naver 뉴스"])

        with google_tab:
            cache = load_news(NEWS_FILE)
            st.markdown(
                f'<div class="mini-caption">마지막 갱신: {html.escape(str(cache.get("updated_at", "알 수 없음")))}</div>',
                unsafe_allow_html=True,
            )
            items = cache.get("items", DEFAULT_NEWS)
            if items:
                st.markdown(news_cards_html(items), unsafe_allow_html=True)
            else:
                st.markdown('<div class="empty-card">표시할 Google 뉴스가 없습니다.</div>', unsafe_allow_html=True)

        with naver_tab:
            cache = load_news(NAVER_NEWS_FILE)
            st.markdown(
                f'<div class="mini-caption">마지막 갱신: {html.escape(str(cache.get("updated_at", "알 수 없음")))}</div>',
                unsafe_allow_html=True,
            )
            if not settings.get("naver_client_id") or not settings.get("naver_client_secret"):
                st.markdown(
                    '<div class="empty-card">Naver 뉴스는 Streamlit Secrets 또는 사이드바에 Naver API 키를 입력하면 사용할 수 있습니다.</div>',
                    unsafe_allow_html=True,
                )
            else:
                items = cache.get("items", DEFAULT_NEWS)
                if items:
                    st.markdown(news_cards_html(items), unsafe_allow_html=True)
                else:
                    st.markdown('<div class="empty-card">표시할 Naver 뉴스가 없습니다.</div>', unsafe_allow_html=True)


def result_html(result: Dict[str, str]) -> str:
    cards = []
    for key in REQUIRED_RESULT_KEYS:
        cards.append(
            f"""
<div class="result-card">
  <strong>{html.escape(key)}</strong>
  <p>{html.escape(result.get(key, "정보 없음"))}</p>
</div>
            """
        )
    return f'<div class="result-grid">{"".join(cards)}</div>'


def history_html(history: List[Dict[str, Any]]) -> str:
    cards: List[str] = []
    for record in history[:12]:
        result = normalize_result(record.get("result", {}))
        image_path = record.get("image_path", "")
        full_path = BASE_DIR / image_path if image_path else None
        data_uri = image_path_to_data_uri(full_path) if full_path and full_path.exists() else ""
        image_tag = (
            f'<img src="{data_uri}" alt="분석 기록 이미지">'
            if data_uri
            else '<div style="height:125px;background:#edf2ed;display:grid;place-items:center;font-size:32px">♻️</div>'
        )

        title = html.escape(result["품목명"])
        created = html.escape(record.get("created_at", ""))

        detail_rows = []
        for key in REQUIRED_RESULT_KEYS:
            detail_rows.append(
                f'<p><b>{html.escape(key)}:</b> '
                f'{html.escape(result.get(key, "정보 없음"))}</p>'
            )

        cards.append(
            f"""
<div class="history-card">
  {image_tag}
  <div class="history-body">
    <h4>{title}</h4>
    <p>{created}</p>
    <p>{html.escape(result["쓰레기 종류"])}</p>
    <details>
      <summary>6가지 분석 내용 모두 보기</summary>
      {"".join(detail_rows)}
    </details>
  </div>
</div>
            """
        )
    return f'<div class="history-grid">{"".join(cards)}</div>'


def render_history() -> None:
    history = read_json(HISTORY_FILE, [])
    if not isinstance(history, list):
        history = []

    title_col, delete_col = st.columns([2.2, 1])
    with title_col:
        st.markdown('<div class="section-heading">🕘 촬영·분석 기록</div>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="mini-caption">최근 기록 {len(history)}개를 저장 중입니다.</div>',
            unsafe_allow_html=True,
        )
    with delete_col:
        if history and st.button("기록 전체 삭제", use_container_width=True):
            delete_all_history()
            st.session_state.pop("latest_result", None)
            st.rerun()

    if history:
        st.markdown(history_html(history), unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty-card">아직 저장된 촬영 기록이 없습니다.</div>', unsafe_allow_html=True)


def render_analysis_panel(settings: Dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown('<div class="section-heading">📷 쓰레기 촬영·분석</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="mini-caption">카메라로 촬영하거나 기존 사진을 올리면 분리배출 방법을 분석합니다.</div>',
            unsafe_allow_html=True,
        )

        camera_file = None
        if "camera_enabled" not in st.session_state:
            st.session_state["camera_enabled"] = False

        camera_button_label = (
            "📴 카메라 끄기"
            if st.session_state["camera_enabled"]
            else "📷 카메라 켜기"
        )
        if st.button(camera_button_label, use_container_width=True):
            st.session_state["camera_enabled"] = not st.session_state["camera_enabled"]
            st.rerun()

        if st.session_state["camera_enabled"]:
            camera_file = st.camera_input("쓰레기를 촬영하세요")
        else:
            st.markdown(
                '<div class="empty-card">카메라가 꺼져 있습니다. 촬영하려면 위 버튼을 눌러주세요.</div>',
                unsafe_allow_html=True,
            )

        st.markdown('<div class="mini-caption" style="margin-top:14px">또는 저장된 사진 파일을 업로드하세요.</div>', unsafe_allow_html=True)
        uploaded_file = st.file_uploader(
            "사진을 선택하세요",
            type=["jpg", "jpeg", "png", "webp"],
        )

        selected_file = camera_file if camera_file is not None else uploaded_file
        item_hint = st.text_input(
            "품목 이름 힌트(선택)",
            placeholder="예: 배달 용기, 페트병, 치킨 상자",
        )

        if selected_file is not None:
            st.image(selected_file, caption="분석할 사진", use_container_width=True)

        if settings["test_mode"]:
            st.info("현재 테스트 모드입니다. 실제 AI 사진 분석은 하지 않습니다.")
        else:
            st.warning(
                f"실제 API 모드입니다. 분석 버튼을 누르면 입력한 "
                f"{settings['provider']} API 키가 사용됩니다."
            )

        if st.button("🔍 쓰레기 분석하기", type="primary", use_container_width=True):
            if selected_file is None:
                st.error("먼저 쓰레기 사진을 촬영하거나 업로드해 주세요.")
            elif not settings["test_mode"] and not str(settings["api_key"]).strip():
                st.error(
                    f"왼쪽 설정에서 {settings['provider']} API 키를 입력해 주세요."
                )
            else:
                image_bytes = selected_file.getvalue()
                mime_type = getattr(selected_file, "type", None) or "image/jpeg"
                with st.spinner("쓰레기 종류와 배출 방법을 분석하는 중입니다..."):
                    try:
                        result = analyze_waste(
                            image_bytes=image_bytes,
                            mime_type=mime_type,
                            item_hint=item_hint,
                            provider=str(settings["provider"]),
                            api_key=str(settings["api_key"]),
                            model_name=str(settings["model_name"]),
                            test_mode=bool(settings["test_mode"]),
                        )
                        add_history(result, image_bytes, mime_type)
                        st.session_state["latest_result"] = result
                        st.success("분석 결과를 기록에 저장했습니다.")
                    except Exception as exc:
                        st.error(f"분석에 실패했습니다: {exc}")

        latest_result = st.session_state.get("latest_result")
        if isinstance(latest_result, dict):
            st.markdown('<div class="section-heading" style="margin:18px 0 12px">✅ 분석 결과</div>', unsafe_allow_html=True)
            st.markdown(result_html(latest_result), unsafe_allow_html=True)

    st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
    render_history()


def main() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="♻️",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    ensure_storage()
    inject_style()
    settings = render_sidebar()

    mode_text = (
        "테스트 모드"
        if settings["test_mode"]
        else f"실제 AI 모드 · {settings['provider']}"
    )
    st.markdown(
        f"""
<div class="hero-wrap">
  <div>
    <h1 class="hero-title">쓰레기는 찍고,<br>배출법은 바로 확인</h1>
    <p class="hero-desc">사진 분석과 관련 뉴스를 한 화면에서 확인하는 분리수거 도우미입니다.</p>
  </div>
  <span class="mode-badge">● {html.escape(mode_text)}</span>
</div>
        """,
        unsafe_allow_html=True,
    )

    news_col, analysis_col = st.columns([0.9, 1.22], gap="large")
    with news_col:
        render_news_panel(settings)
    with analysis_col:
        render_analysis_panel(settings)

    st.markdown(
        "<div style='padding:28px 0 4px;text-align:center;color:#657168;font-size:12px'>"
        "실제 배출 기준과 과태료 적용은 지자체별로 다를 수 있으므로 거주 지역의 공식 안내를 확인하세요."
        "</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
