import base64
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional

import streamlit as st


APP_TITLE = "자취생 필수! 스마트 분리수거 가이드"

REQUIRED_FIELDS = [
    "품목 분류",
    "배출 방법 (핵심)",
    "자취생 꿀팁",
    "난이도",
    "관련 법안/과태료 한 줄 요약",
]

FALLBACK_NEWS = [
    {
        "date": "2026.01",
        "title": "투명 페트병 별도 배출 집중 홍보",
        "summary": "라벨 제거, 내용물 비우기, 압착 후 뚜껑 닫기 등 올바른 배출법 안내가 강화되고 있어요.",
        "source": "예시 데이터",
        "link": "",
    },
    {
        "date": "2025.11",
        "title": "일회용품 사용 줄이기 캠페인 확대",
        "summary": "카페, 배달, 편의점 이용 시 다회용기와 개인 컵 사용을 장려하는 지자체 캠페인이 늘고 있어요.",
        "source": "예시 데이터",
        "link": "",
    },
    {
        "date": "2025.08",
        "title": "종량제 봉투 미사용 및 혼합배출 단속 안내",
        "summary": "재활용품에 음식물이나 이물질이 많이 섞이면 수거 거부 또는 과태료 대상이 될 수 있어요.",
        "source": "예시 데이터",
        "link": "",
    },
]

GEMINI_FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash-latest",
]


def get_secret_or_env(name: str) -> Optional[str]:
    """Read optional keys safely from env or Streamlit secrets."""
    if os.getenv(name):
        return os.getenv(name)

    try:
        return st.secrets.get(name)
    except Exception:
        return None


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.replace("&quot;", '"').replace("&amp;", "&").strip()


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_google_news(query: str, max_items: int = 5) -> List[Dict[str, str]]:
    """Fetch recent recycling news from Google News RSS."""
    encoded_query = urllib.parse.quote(query)
    url = (
        "https://news.google.com/rss/search?"
        f"q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
    )

    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SmartRecycleGuide/1.0"},
    )

    with urllib.request.urlopen(request, timeout=7) as response:
        xml_data = response.read()

    root = ET.fromstring(xml_data)
    items: List[Dict[str, str]] = []

    for item in root.findall(".//item")[:max_items]:
        title = strip_html(item.findtext("title", "제목 없음"))
        summary = strip_html(item.findtext("description", ""))
        link = item.findtext("link", "")
        source = item.findtext("source", "Google News")
        pub_date = item.findtext("pubDate", "")

        try:
            date_text = parsedate_to_datetime(pub_date).strftime("%Y.%m.%d")
        except Exception:
            date_text = datetime.now().strftime("%Y.%m.%d")

        items.append(
            {
                "date": date_text,
                "title": title,
                "summary": summary[:120] + ("..." if len(summary) > 120 else ""),
                "source": source,
                "link": link,
            }
        )

    return items


@st.cache_data(ttl=60 * 60, show_spinner=False)
def fetch_naver_news(
    query: str,
    max_items: int = 5,
    _client_id: Optional[str] = None,
    _client_secret: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Fetch recent news from Naver Search API."""
    if not _client_id or not _client_secret:
        raise ValueError("Streamlit Secrets에 NAVER_CLIENT_ID와 NAVER_CLIENT_SECRET을 설정해 주세요.")

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
            "X-Naver-Client-Id": _client_id,
            "X-Naver-Client-Secret": _client_secret,
            "User-Agent": "SmartRecycleGuide/1.0",
        },
    )

    with urllib.request.urlopen(request, timeout=7) as response:
        payload = json.loads(response.read().decode("utf-8"))

    items: List[Dict[str, str]] = []
    for item in payload.get("items", [])[:max_items]:
        pub_date = item.get("pubDate", "")
        try:
            date_text = parsedate_to_datetime(pub_date).strftime("%Y.%m.%d")
        except Exception:
            date_text = datetime.now().strftime("%Y.%m.%d")

        items.append(
            {
                "date": date_text,
                "title": strip_html(item.get("title", "제목 없음")),
                "summary": strip_html(item.get("description", "")),
                "source": "Naver News",
                "link": item.get("originallink") or item.get("link", ""),
            }
        )

    return items


def render_news_cards(timeline_items: List[Dict[str, str]]) -> None:
    for item in timeline_items:
        with st.container(border=True):
            st.markdown(f"**{item['date']} · {item['title']}**")
            if item.get("summary"):
                st.markdown(item["summary"])
            st.caption(f"출처: {item.get('source', '뉴스')}")
            if item.get("link"):
                st.link_button("기사 열기", item["link"], use_container_width=True)


def render_news_timeline(
    auto_fetch_news: bool,
    news_query: str,
    naver_client_id: Optional[str],
    naver_client_secret: Optional[str],
) -> None:
    """Render Google/Naver news tabs with fallback data."""
    st.subheader("📰 최신 분리수거 뉴스 & 법안 타임라인")

    if st.button("🔄 뉴스 새로고침", use_container_width=True):
        fetch_google_news.clear()
        fetch_naver_news.clear()
        st.rerun()

    google_tab, naver_tab = st.tabs(["Google 뉴스", "Naver 뉴스"])

    with google_tab:
        if auto_fetch_news:
            try:
                google_items = fetch_google_news(news_query)
                st.caption("Google News RSS에서 최신 기사를 가져왔어요.")
            except Exception as exc:
                google_items = FALLBACK_NEWS
                st.warning(f"Google 뉴스 수집에 실패해서 예시 데이터를 보여드려요. ({exc})")
        else:
            google_items = FALLBACK_NEWS
            st.caption("자동 수집이 꺼져 있어 예시 데이터를 표시 중이에요.")

        render_news_cards(google_items)

    with naver_tab:
        if auto_fetch_news:
            try:
                naver_items = fetch_naver_news(
                    news_query,
                    _client_id=naver_client_id,
                    _client_secret=naver_client_secret,
                )
                st.caption("Naver 검색 API에서 최신 기사를 가져왔어요.")
            except Exception as exc:
                naver_items = FALLBACK_NEWS
                st.warning(f"Naver 뉴스 수집에 실패해서 예시 데이터를 보여드려요. ({exc})")
        else:
            naver_items = FALLBACK_NEWS
            st.caption("자동 수집이 꺼져 있어 예시 데이터를 표시 중이에요.")

        render_news_cards(naver_items)


def build_ai_prompt(item_name: Optional[str], has_image: bool) -> str:
    input_hint = item_name.strip() if item_name else "업로드된 쓰레기 이미지"
    image_hint = "이미지도 함께 참고해 주세요." if has_image else "텍스트 정보만 참고해 주세요."

    return f"""
당신은 한국의 생활폐기물 분리배출을 안내하는 친절한 전문가입니다.
사용자가 입력한 품목은 "{input_hint}"입니다. {image_hint}

아래 5가지 항목을 반드시 JSON 객체로만 반환해 주세요.
모르는 경우에는 추정이라고 명확히 적고, 지역별 차이가 있을 수 있음을 짧게 안내해 주세요.

필수 키:
1. 품목 분류
2. 배출 방법 (핵심)
3. 자취생 꿀팁
4. 난이도
5. 관련 법안/과태료 한 줄 요약
""".strip()


def extract_json_object(raw_text: str) -> Dict[str, str]:
    """Parse a model response that should contain a JSON object."""
    cleaned = raw_text.strip()

    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.removeprefix("json").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def get_uploaded_image_base64(uploaded_file) -> Optional[str]:
    if uploaded_file is None:
        return None
    return base64.b64encode(uploaded_file.getvalue()).decode("utf-8")


def call_gemini_api(
    item_name: Optional[str],
    uploaded_file,
    api_key: str,
    model_name: str,
) -> Dict[str, str]:
    """Call Google Gemini API and return a normalized recycling guide result."""
    if not api_key:
        raise ValueError("Gemini API 키를 입력해 주세요.")

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    prompt = build_ai_prompt(item_name, uploaded_file is not None)
    contents = [prompt]

    if uploaded_file is not None:
        contents.append(
            {
                "mime_type": uploaded_file.type,
                "data": uploaded_file.getvalue(),
            }
        )

    candidate_models = []
    for candidate in [model_name, *GEMINI_FALLBACK_MODELS]:
        if candidate and candidate not in candidate_models:
            candidate_models.append(candidate)

    last_error = None
    for candidate in candidate_models:
        try:
            model = genai.GenerativeModel(candidate)
            response = model.generate_content(contents)
            parsed = extract_json_object(response.text)
            return normalize_result(parsed)
        except Exception as exc:
            last_error = exc

    raise RuntimeError(
        "Gemini 모델 호출에 실패했어요. 사이드바 모델명을 gemini-2.5-flash 또는 "
        f"gemini-2.0-flash로 바꿔 다시 시도해 주세요. 마지막 오류: {last_error}"
    )


def call_openai_api(
    item_name: Optional[str],
    uploaded_file,
    api_key: str,
    model_name: str,
) -> Dict[str, str]:
    """Call OpenAI API and return a normalized recycling guide result."""
    if not api_key:
        raise ValueError("OpenAI API 키를 입력해 주세요.")

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    prompt = build_ai_prompt(item_name, uploaded_file is not None)

    content = [{"type": "text", "text": prompt}]
    if uploaded_file is not None:
        image_base64 = get_uploaded_image_base64(uploaded_file)
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{uploaded_file.type};base64,{image_base64}"},
            }
        )

    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": content}],
        response_format={"type": "json_object"},
    )

    parsed = extract_json_object(response.choices[0].message.content or "")
    return normalize_result(parsed)


def get_mock_result(item_name: Optional[str] = None, has_image: bool = False) -> Dict[str, str]:
    """Return deterministic mock data so the UI works without an API key."""
    name = (item_name or "").strip()

    if "치킨" in name or "피자" in name or "상자" in name:
        return {
            "품목 분류": "종이 또는 일반쓰레기",
            "배출 방법 (핵심)": "기름기와 음식물이 묻은 부분은 일반쓰레기로 버리고, 깨끗한 종이 부분만 펼쳐서 종이류로 배출하세요.",
            "자취생 꿀팁": "양념, 치즈, 기름이 스며든 상자는 재활용 품질을 떨어뜨려요. 애매하면 오염된 부분만 잘라내는 것이 좋아요.",
            "난이도": "보통",
            "관련 법안/과태료 한 줄 요약": "재활용품에 음식물 등 이물질을 섞어 배출하면 지자체 기준에 따라 수거 거부나 과태료 대상이 될 수 있어요.",
        }

    if "컵라면" in name or "라면" in name:
        return {
            "품목 분류": "일반쓰레기 또는 플라스틱",
            "배출 방법 (핵심)": "국물과 음식물을 완전히 비우고 씻어도 색이나 냄새가 남으면 일반쓰레기로, 깨끗한 플라스틱 용기는 플라스틱류로 배출하세요.",
            "자취생 꿀팁": "컵라면 용기는 양념 색이 잘 배어 재활용이 까다로운 대표 품목이에요. 스티로폼 재질도 오염되면 일반쓰레기예요.",
            "난이도": "까다로움",
            "관련 법안/과태료 한 줄 요약": "오염된 재활용품은 재활용 선별을 방해하므로 혼합배출 시 불이익이 생길 수 있어요.",
        }

    if "배달" in name or "용기" in name:
        return {
            "품목 분류": "플라스틱",
            "배출 방법 (핵심)": "음식물을 비우고 물로 헹군 뒤, 비닐 라벨이나 스티커는 가능한 제거해서 플라스틱류로 배출하세요.",
            "자취생 꿀팁": "빨간 양념이나 기름이 남아 있으면 재활용이 어려워요. 세척이 어렵다면 일반쓰레기로 분류하는 편이 안전해요.",
            "난이도": "보통",
            "관련 법안/과태료 한 줄 요약": "재활용품은 내용물을 비우고 이물질을 제거한 뒤 배출하는 것이 기본 원칙이에요.",
        }

    if has_image:
        return {
            "품목 분류": "테스트 모드: 이미지 분석 대기",
            "배출 방법 (핵심)": "현재는 API 없이 UI를 확인하는 상태예요. 실제 이미지 분류는 API 키 입력 후 테스트 모드를 끄면 작동합니다.",
            "자취생 꿀팁": "사진 분석 결과처럼 보이는 목업을 표시 중이에요. 팀원에게 공유해도 API 비용은 발생하지 않습니다.",
            "난이도": "보통",
            "관련 법안/과태료 한 줄 요약": "실제 배출 전에는 지역별 분리배출 기준을 확인하는 것이 좋아요.",
        }

    return {
        "품목 분류": "추정: 플라스틱 / 종이 / 일반쓰레기 중 확인 필요",
        "배출 방법 (핵심)": "내용물을 완전히 비우고, 물로 헹군 뒤, 라벨·스티커·뚜껑처럼 재질이 다른 부분은 가능한 분리하세요.",
        "자취생 꿀팁": "품목명이 애매하면 재질 표시를 먼저 확인하세요. 오염이 심한 품목은 재활용보다 일반쓰레기일 가능성이 높아요.",
        "난이도": "보통",
        "관련 법안/과태료 한 줄 요약": "분리배출 기준은 지자체별로 조금 다르며, 혼합배출·무단투기는 과태료 대상이 될 수 있어요.",
    }


def normalize_result(result: Dict[str, str]) -> Dict[str, str]:
    return {field: str(result.get(field, "정보 없음")) for field in REQUIRED_FIELDS}


def analyze_recycling(
    item_name: Optional[str],
    uploaded_file,
    use_mock: bool,
    provider: str,
    api_key: str,
    model_name: str,
) -> Dict[str, str]:
    if use_mock:
        return get_mock_result(item_name, uploaded_file is not None)

    if provider == "OpenAI":
        return call_openai_api(item_name, uploaded_file, api_key, model_name)

    return call_gemini_api(item_name, uploaded_file, api_key, model_name)


def render_result(result: Dict[str, str]) -> None:
    st.subheader("✅ AI 분리수거 분석 결과")

    # Avoid st.table because some local pandas/numpy installs can fail with
    # binary compatibility errors.
    for key in REQUIRED_FIELDS:
        with st.container(border=True):
            st.markdown(f"**{key}**")
            st.markdown(result[key])

    difficulty = result.get("난이도", "")
    if "쉬움" in difficulty:
        st.success("오늘의 분리수거 난이도는 쉬움이에요. 가볍게 처리 가능! 🌱")
    elif "까다" in difficulty:
        st.warning("조금 까다로운 품목이에요. 오염 여부와 재질 표시를 꼭 확인해 주세요. 🔍")
    else:
        st.info("기본 원칙만 지키면 충분히 처리할 수 있어요. 🧼")


def render_settings_panel() -> Dict[str, object]:
    st.sidebar.header("⚙️ 설정")

    st.sidebar.subheader("AI 연결")
    use_mock = st.sidebar.toggle(
        "테스트 모드 사용(API 비용 없음)",
        value=True,
        help="켜져 있으면 API를 호출하지 않고 목업 결과만 보여줍니다.",
    )

    provider = st.sidebar.selectbox("AI 제공자", ["Gemini", "OpenAI"])

    default_model = "gemini-2.5-flash" if provider == "Gemini" else "gpt-4.1-mini"
    model_name = st.sidebar.text_input("모델명", value=default_model)

    env_key_name = "GEMINI_API_KEY" if provider == "Gemini" else "OPENAI_API_KEY"
    existing_key = get_secret_or_env(env_key_name) or ""
    api_key = st.sidebar.text_input(
        f"{provider} API 키",
        value=existing_key,
        type="password",
        placeholder="각자 본인의 API 키를 입력하세요",
        help="입력한 키는 코드에 저장되지 않습니다. Streamlit 실행 세션에서만 사용됩니다.",
    )

    if use_mock:
        st.sidebar.info("현재 테스트 모드라서 API 키를 입력해도 호출하지 않아요.")
    elif not api_key:
        st.sidebar.warning("실제 AI 분석을 하려면 API 키가 필요해요.")
    else:
        st.sidebar.success("API 키가 입력되었습니다. 테스트 모드를 끄면 실제 AI 분석을 호출합니다.")

    st.sidebar.divider()

    st.sidebar.subheader("뉴스")
    auto_fetch_news = st.sidebar.toggle("인터넷 기사 자동 수집", value=True)
    news_query = st.sidebar.text_input(
        "뉴스 검색어",
        value="분리수거 재활용 과태료 배출",
    )

    naver_client_id = get_secret_or_env("NAVER_CLIENT_ID") or ""
    naver_client_secret = get_secret_or_env("NAVER_CLIENT_SECRET") or ""

    if naver_client_id and naver_client_secret:
        st.sidebar.success("Naver 검색 API 키가 Secrets에 설정되어 있어요.")
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

    return {
        "use_mock": use_mock,
        "provider": provider,
        "api_key": api_key,
        "model_name": model_name,
        "auto_fetch_news": auto_fetch_news,
        "news_query": news_query,
        "naver_client_id": naver_client_id,
        "naver_client_secret": naver_client_secret,
    }


def render_ai_panel(settings: Dict[str, object]) -> None:
    st.subheader("📸 AI 스마트 분리수거 카메라")
    st.caption("사진을 올리거나 물건 이름을 입력하면 분리배출 방법을 알려드려요.")

    camera_file = st.camera_input(
        "📷 분리수거할 물건을 카메라로 찍어주세요",
        help="스마트폰 카메라나 노트북 웹캠으로 바로 촬영할 수 있어요.",
    )

    uploaded_file = st.file_uploader(
        "쓰레기 사진 업로드",
        type=["jpg", "jpeg", "png", "webp"],
        help="예: 배달 용기, 컵라면 용기, 페트병, 택배 상자 사진",
    )

    analysis_file = camera_file or uploaded_file

    if camera_file is not None:
        st.success("방금 촬영한 사진을 분석에 사용할게요.")
    elif uploaded_file is not None:
        st.image(uploaded_file, caption="업로드한 이미지", use_container_width=True)

    item_name = st.text_input(
        "물건 이름 검색",
        placeholder='예: "치킨 상자", "배달 용기", "컵라면 용기"',
    )

    if settings["use_mock"]:
        st.info("🧪 테스트 모드 ON: 버튼을 눌러도 API 비용이 발생하지 않습니다.")
    else:
        st.warning("💸 실제 API 모드 ON: 버튼을 누르면 입력한 API 키로 비용이 발생할 수 있습니다.")

    if st.button("🔎 분리수거 방법 확인하기", type="primary", use_container_width=True):
        if analysis_file is None and not item_name.strip():
            st.error("카메라로 사진을 찍거나, 사진을 업로드하거나, 물건 이름을 입력해 주세요.")
            return

        with st.spinner("분리배출 방법을 확인하는 중이에요..."):
            try:
                result = analyze_recycling(
                    item_name=item_name,
                    uploaded_file=analysis_file,
                    use_mock=bool(settings["use_mock"]),
                    provider=str(settings["provider"]),
                    api_key=str(settings["api_key"]),
                    model_name=str(settings["model_name"]),
                )
            except Exception as exc:
                st.error(f"AI API 호출에 실패했어요: {exc}")
                st.info("대신 테스트 모드 목업 결과를 보여드릴게요.")
                result = get_mock_result(item_name, analysis_file is not None)

        render_result(result)


def render_main_ui() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        page_icon="♻️",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    settings = render_settings_panel()

    st.title(f"♻️ {APP_TITLE}")
    st.markdown("대학생과 자취생을 위한 빠르고 친근한 분리배출 도우미")
    st.divider()

    left_col, right_col = st.columns([1, 1], gap="large")

    with left_col:
        render_news_timeline(
            auto_fetch_news=bool(settings["auto_fetch_news"]),
            news_query=str(settings["news_query"]),
            naver_client_id=str(settings["naver_client_id"]),
            naver_client_secret=str(settings["naver_client_secret"]),
        )

    with right_col:
        render_ai_panel(settings)

    st.divider()
    st.caption(
        "※ 실제 배출 기준은 지자체별로 다를 수 있어요. 최종 배출 전 거주 지역 안내를 함께 확인해 주세요."
    )


if __name__ == "__main__":
    render_main_ui()
