import streamlit as st
from slack_sdk import WebClient
from datetime import datetime, timedelta, timezone
import time
KST = timezone(timedelta(hours=9))  # KST: UTC+9
import os
from dotenv import load_dotenv  # 추가
load_dotenv()  # 추가

import re
from openai import OpenAI
# Google Gemini API 추가
from google import genai
from slack_sdk.errors import SlackApiError
from slack_sdk.web import WebClient
from slack_sdk.web.slack_response import SlackResponse

def safe_api_call(func, *args, **kwargs):
    while True:
        try:
            return func(*args, **kwargs)
        except SlackApiError as e:
            error = e.response.get("error", "")
            if error == "ratelimited":
                retry_after = int(e.response.headers.get("Retry-After", 1))
                print(f"Rate limited. Retrying after {retry_after} seconds...")
                time.sleep(retry_after)
                continue
            else:
                print(f"Slack API error: {error}")
                raise e

SLACK_TOKEN = os.getenv("SLACK_USER_TOKEN")
if not SLACK_TOKEN:
    raise ValueError("SLACK_USER_TOKEN 환경변수가 설정되지 않았습니다.")

# OpenAI 클라이언트 초기화
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Google Gemini 설정
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    # 사용 가능한 모델 목록 출력
    print("사용 가능한 Gemini 모델:")
    for model in gemini_client.models.list():
        print(f"- {model.name}")

client = WebClient(token=SLACK_TOKEN)

def resolve_user_name(m, user_map, lookup=False):
    user_id = m.get("user")
    if user_id in user_map:
        return user_map[user_id]
    elif lookup and user_id:
        try:
            res = client.users_info(user=user_id)
            if res.get("ok") and "user" in res:
                profile = res["user"].get("profile", {})
                return profile.get("real_name") or profile.get("display_name") or res["user"].get("name") or user_id
        except Exception as e:
            print(f"users_info 실패: {user_id} - {e}")
        return f"(알 수 없음: {user_id})"
    elif "username" in m:
        return m["username"]
    elif m.get("bot_profile", {}).get("name"):
        return f"{m['bot_profile']['name']} (bot)"
    else:
        return f"(알 수 없음: {m.get('user') or m.get('bot_id') or 'unknown'})"

@st.cache_data(show_spinner=False, ttl=30*60, persist=False)
def get_all_channels(max_pages=10):
    all_channels = []
    cursor = None
    pages = 0
    
    # spinner 제거 (메인에서 이미 표시하므로)
    while pages < max_pages:
        try:
            # 가입된 채널만 가져오기
            resp = safe_api_call(client.conversations_list,
                                types="public_channel,private_channel",
                                limit=1000,
                                exclude_archived=False,
                                cursor=cursor)
            
            if not resp or not resp.get("ok"):
                st.error(f"채널 목록 가져오기 실패: {resp.get('error', '알 수 없는 오류')}")
                break
            
            # 멤버로 가입된 채널만 필터링
            for ch in resp["channels"]:
                if ch.get("is_member"):
                    # 필요한 필드만 저장 (디버깅 정보 제거)
                    all_channels.append({
                        "id": ch.get("id"),
                        "name": ch.get("name"),
                        "topic": ch.get("topic", {}).get("value", ""),
                        "purpose": ch.get("purpose", {}).get("value", ""),
                        "is_private": ch.get("is_private", False),
                        "is_member": True
                    })
            
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
            
            pages += 1
            time.sleep(0.3)
        except Exception as e:
            st.error(f"채널 로드 중 오류 발생: {str(e)}")
            break
    
    return all_channels


def fetch_thread_replies(channel_id, thread_ts):
    response = client.conversations_replies(
        channel=channel_id,
        ts=thread_ts
    )
    if not response.get("ok", True):
        raise Exception(f"Slack API error in conversations.replies: {response.get('error')}")
    return response["messages"]


def parse_slack_link(link: str):
    match = re.search(r"archives/([A-Z0-9]+)/p(\d{10})(\d+)", link)
    if match:
        channel = match.group(1)
        ts = f"{match.group(2)}.{match.group(3)}"
        return channel, ts
    return None, None

# Slack 스레드 URL 생성 함수 추가
def get_slack_thread_url(team_id, channel_id, thread_ts):
    """Slack 스레드로 이동할 수 있는 URL을 생성합니다."""
    # team_id 매개변수는 실제로 team_name 또는 team_id 중 하나가 전달됨
    # T로 시작하는지 체크하지 않고 바로 사용
    workspace_url = f"{team_id}.slack.com"
    
    # Slack URL은 타임스탬프에서 소수점을 제거하고 p를 앞에 붙이는 특별한 형식 사용
    if '.' in thread_ts:
        # 소수점 형태의 타임스탬프 처리 (예: 1234567890.123456)
        # Slack URL 형식: p1234567890123456
        ts_without_dot = thread_ts.replace('.', '')
        slack_url = f"https://{workspace_url}/archives/{channel_id}/p{ts_without_dot}"
        print(f"Thread URL 생성: ts={thread_ts}, 변환={ts_without_dot}, url={slack_url}, team_id/name={team_id}")
        return slack_url
    else:
        # 소수점이 없는 경우 그대로 사용
        slack_url = f"https://{workspace_url}/archives/{channel_id}/p{thread_ts}"
        print(f"Thread URL 생성(소수점 없음): ts={thread_ts}, url={slack_url}, team_id/name={team_id}")
        return slack_url

# OpenAI API를 사용한 요약 함수
def summarize_with_openai(context, prompt):
    """OpenAI API를 사용하여 텍스트 요약"""
    try:
        res = openai_client.chat.completions.create(
            model="gpt-4",  # 또는 gpt-3.5-turbo
            messages=[{"role": "user", "content": prompt}]
        )
        return res.choices[0].message.content
    except Exception as e:
        print(f"OpenAI API 오류: {str(e)}")
        return f"OpenAI 요약 생성 중 오류가 발생했습니다: {str(e)}"

# Google Gemini API를 사용한 요약 함수
def summarize_with_gemini(context, prompt):
    """Google Gemini API를 사용하여 텍스트 요약"""
    try:
        # API 키 확인
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not gemini_api_key:
            return "GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다."
            
        # Gemini 모델 설정
        client = genai.Client(api_key=gemini_api_key)
        
        # 요약 생성
        response = client.models.generate_content(
            model='models/gemini-2.5-flash-preview-04-17',
            contents=prompt
        )
        return response.text
    except Exception as e:
        print(f"Gemini API 오류: {str(e)}")
        return f"Gemini 요약 생성 중 오류가 발생했습니다: {str(e)}"

def summarize_thread(thread_messages, user_map):
    """스레드 메시지를 요약하는 함수"""
    lines = []
    for m in thread_messages:
        # 사용자 ID를 이름으로 변환
        user_id = m.get("user", "(알 수 없음)")
        user_name = resolve_user_name(m, user_map, lookup=True)
        
        # 텍스트가 None인 경우 처리
        text = m.get('text') or ''
        
        # 소스 코드 블록이나 HTML 태그가 포함된 경우 처리
        # 간단한 이스케이프 처리로 요약 품질 향상
        text = text.replace("```", "[코드 블록]")
        text = text.replace("`", "[코드]")
        
        lines.append(f"{user_name}: {text}")
        
    context = "\n".join(lines)
    prompt = f"""다음 Slack 스레드 내용을 한 문장으로 요약해 주세요:

{context}

요약할 때 다음 사항을 지켜주세요:
1. 코드 블록이나 코드 조각은 '코드' 또는 '소스코드'라고만 언급하고 실제 코드는 포함하지 마세요
2. HTML, CSS, JavaScript 등의 태그 구문은 제외하고 요약해주세요
3. 짧고 간결하게 핵심만 요약해주세요
4. 꼭 1-2 문장으로 제한하여 요약해주세요
"""

    # 설정된 AI 모델에 따라 요약 함수 호출
    ai_model = st.session_state.get('ai_model', 'gemini')
    
    if ai_model == 'gemini':
        return summarize_with_gemini(context, prompt)
    else:  # 기본값은 OpenAI
        return summarize_with_openai(context, prompt)

if __name__ == "__main__":
    # 페이지 설정 - 넓은 레이아웃 사용
    st.set_page_config(
        page_title="Slack 업무 요약 도우미",
        layout="wide",
        initial_sidebar_state="expanded"
    )
    
    # 사이드바 너비 최대화 CSS 추가
    st.markdown("""
    <style>
        [data-testid="stSidebar"] {
            min-width: 400px;
            max-width: 500px;
        }
    </style>
    """, unsafe_allow_html=True)
    
    st.title("Slack 업무 요약 도우미")
    
    # 사이드바에 설정 옵션 배치
    with st.sidebar:
        st.header("설정")
        
        # 앱 설명 및 사용법 추가
        with st.expander("프로그램 설명 및 사용법", expanded=True):
            st.markdown("""
            ### 🔍 Slack 업무 요약 도우미란?
            
            이 도구는 Slack 채널의 스레드를 자동으로 요약해주는 애플리케이션입니다. 
            GPT-4를 활용하여 스레드의 대화 내용을 분석하고 핵심 내용을 요약합니다.
            
            ### 📝 사용 방법
            
            1. **날짜 설정**: 요약할 스레드의 시작 날짜를 선택하세요.
            2. **채널 필터링**: 특정 키워드로 채널을 필터링할 수 있습니다.
            3. **채널 선택**: 요약할 채널을 선택하세요.
            4. **스레드 수 설정**: 채널당 최대 스레드 수를 설정하세요.
            5. **요약 시작**: 버튼을 클릭하면 선택한 채널의 스레드를 요약합니다.
            
            ### 💡 팁
            
            - 여러 채널을 동시에 선택할 수 있습니다.
            - 스레드 바로가기 링크를 통해 원본 내용을 확인할 수 있습니다.
            - 캐시 비우기 버튼으로 채널 정보를 새로고침할 수 있습니다.
            """)
        
        # 구분선 추가
        st.markdown("---")
        
        # AI 모델 선택 옵션
        st.subheader("AI 모델 설정")
        
        # 세션 상태 초기화 (ai_model이 없는 경우)
        if 'ai_model' not in st.session_state:
            st.session_state.ai_model = 'gemini'
        
        model_option = st.radio(
            "요약에 사용할 AI 모델을 선택하세요:",
            options=["OpenAI (GPT-4)", "Google Gemini"],
            index=1 if st.session_state.ai_model == 'gemini' else 0,
            horizontal=True
        )
        
        # 선택된 옵션에 따라 세션 상태 업데이트
        st.session_state.ai_model = 'gemini' if model_option == "Google Gemini" else 'openai'
        
        # 선택된 모델에 대한 API 키 가이드
        if st.session_state.ai_model == 'openai':
            if not os.getenv("OPENAI_API_KEY"):
                st.warning("⚠️ OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다.")
            else:
                st.success("✅ OpenAI API 키가 설정되어 있습니다.")
        else:  # gemini
            if not os.getenv("GEMINI_API_KEY"):
                st.warning("⚠️ GEMINI_API_KEY 환경변수가 설정되어 있지 않습니다.")
                st.info("Google AI Studio(https://makersuite.google.com/)에서 API 키를 발급받을 수 있습니다.")
            else:
                st.success("✅ Google Gemini API 키가 설정되어 있습니다.")
        
        # 구분선 추가
        st.markdown("---")
        
        # 캐시 비우기 옵션 추가
        if st.button("캐시 비우기 및 새로고침"):
            st.cache_data.clear()
            st.rerun()

        # 기본 설정
        st.subheader("날짜 설정")
        date_input = st.date_input("요약 시작 기준 날짜를 선택하세요")
        dt = datetime.combine(date_input, datetime.min.time())
        since_ts = dt.replace(tzinfo=KST).timestamp()
        
        # 채널 필터링 설정
        st.subheader("채널 필터링")
        filter_input = st.text_input(
            "채널 필터링 키워드 입력 \n\n(쉼표로 구분, 예: subscription)", ""
        )
        
        # 필터링 옵션 (간소화)
        case_sensitive = st.checkbox("대소문자 구분", value=False)
    
    # 메인 영역 - 채널 로드 및 필터링 결과 표시
    user_map = {}  # no preloading

    # Slack API에서 팀 정보 가져오기 (URL 생성용)
    try:
        auth_test = client.auth_test()
        # auth_test 결과에서 team_id와 team(팀 이름) 필드를 가져옴
        st.session_state.team_id = auth_test.get("team_id", "")
        st.session_state.team_name = auth_test.get("team", "")  # team 필드가 팀 이름(subdomain)을 포함
        print(f"Slack 팀 정보: team_id={st.session_state.team_id}, team_name={st.session_state.team_name}")
    except Exception as e:
        # 오류 출력 및 기본값 사용
        print(f"팀 정보 가져오기 오류: {str(e)}")
        if "team_id" not in st.session_state:
            st.session_state.team_id = "T0123456"  # 기본값
        if "team_name" not in st.session_state:
            st.session_state.team_name = "workspace"  # 기본값

    # 멤버로 가입된 채널만 로드
    with st.spinner("가입된 채널 정보 로드 중..."):
        channel_objs = get_all_channels()
    
    # 채널별 최근 메시지 확인 (선택한 날짜 이후)
    with st.spinner("채널별 메시지 확인 중..."):
        # 각 채널마다 선택한 날짜 이후 메시지가 있는지 확인
        channels_with_messages = set()
        
        # 모든 채널을 확인하는 것은 시간이 오래 걸릴 수 있으므로 캐시 사용
        @st.cache_data(ttl=60*5)  # 5분 캐시
        def check_channels_with_messages(channel_ids, since_timestamp):
            result = set()
            for channel_id in channel_ids:
                try:
                    # 각 채널의 최근 메시지 하나만 가져와서 확인 (API 호출 최소화)
                    response = client.conversations_history(
                        channel=channel_id, 
                        oldest=since_timestamp,
                        limit=1  # 메시지 하나만 확인
                    )
                    if response["messages"]:
                        result.add(channel_id)
                except Exception as e:
                    print(f"채널 {channel_id} 메시지 확인 중 오류: {e}")
                    continue
            return result
        
        # 채널 ID 목록 추출
        channel_ids = [ch["id"] for ch in channel_objs]
        # 날짜 이후 메시지가 있는 채널 ID 집합 가져오기
        channels_with_messages = check_channels_with_messages(channel_ids, since_ts)
        
        # 채널 객체에 메시지 있음 표시 추가
        for ch in channel_objs:
            ch["has_messages"] = ch["id"] in channels_with_messages
    
    # 채널 정보 출력 (간소화)
    st.info(f"총 {len(channel_objs)}개 가입된 채널 로드됨")
    
    # 최근 메시지 있는 채널 수 표시
    st.success(f"📢 선택한 날짜 이후 메시지가 있는 채널: {len(channels_with_messages)}개")
    
    # 메시지가 있는 채널만 표시할지 선택하는 체크박스
    show_only_active = st.checkbox("📢 메시지가 있는 채널만 표시", value=True)
    
    # 채널 선택 저장용 세션 상태
    if 'selected_for_summary' not in st.session_state:
        st.session_state.selected_for_summary = set()
    
    # 채널 상세 정보를 확인할 수 있는 확장(expander) 추가
    with st.expander("👁️ 가입된 채널 상세 정보 확인하기", expanded=False):
        # 일괄 선택/해제 버튼들
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            if st.button("🔘 모두 선택", use_container_width=True):
                for ch in channel_objs:
                    if show_only_active and not ch.get("has_messages", False):
                        continue
                    st.session_state.selected_for_summary.add(ch["name"])
        with col2:
            if st.button("⚪ 모두 해제", use_container_width=True):
                st.session_state.selected_for_summary.clear()
        
        # 필터링된 채널 목록 준비
        filtered_for_display = []
        for ch in channel_objs:
            # 메시지가 있는 채널만 표시 옵션이 켜져 있고, 메시지가 없는 경우 건너뛰기
            if show_only_active and not ch.get("has_messages", False):
                continue
            filtered_for_display.append(ch)
        
        # 채널 데이터를 DataFrame으로 표시하지 않고, 체크박스가 있는 형태로 UI 구성
        st.write("#### 채널 목록")
        
        if not filtered_for_display:
            st.warning("표시할 채널이 없습니다.")
        else:
            # 채널 목록을 여러 컬럼으로 나누어 표시
            num_columns = 2  # 가로로 2개 컬럼 사용
            channels_per_column = len(filtered_for_display) // num_columns + (1 if len(filtered_for_display) % num_columns else 0)
            
            # 컬럼 생성
            channel_cols = st.columns(num_columns)
            
            # 각 컬럼에 할당된 채널 표시
            for i, ch in enumerate(filtered_for_display):
                channel_name = ch["name"]
                has_messages = ch.get("has_messages", False)
                is_private = ch.get("is_private", False)
                
                # 채널명 앞에 표시할 이모지
                emoji = "📢" if has_messages else "💤"
                lock = "🔒" if is_private else "🔓"
                
                # 컬럼 인덱스 계산
                col_idx = i // channels_per_column
                if col_idx >= num_columns:
                    col_idx = num_columns - 1
                
                # 체크박스 상태가 변경되면 세션 상태 업데이트
                checkbox_key = f"channel_select_{channel_name}"
                with channel_cols[col_idx]:
                    if st.checkbox(
                        f"{emoji} {lock} {channel_name}", 
                        value=channel_name in st.session_state.selected_for_summary,
                        key=checkbox_key
                    ):
                        st.session_state.selected_for_summary.add(channel_name)
                    else:
                        if channel_name in st.session_state.selected_for_summary:
                            st.session_state.selected_for_summary.remove(channel_name)
    
    # 필터링 구현
    if filter_input:
        keywords = [kw.strip() for kw in filter_input.split(",") if kw.strip()]
        
        if not case_sensitive:
            # 대소문자 구분 안 함
            keywords = [kw.lower() for kw in keywords]
        
        # 필터링 로직 간소화
        filtered_channels = []
        
        for ch in channel_objs:
            # 채널 정보 추출
            ch_name = ch.get("name", "")
            topic_value = ch.get("topic", "") if isinstance(ch.get("topic"), str) else ""
            purpose_value = ch.get("purpose", "") if isinstance(ch.get("purpose"), str) else ""
            
            # 대소문자 구분 옵션 처리
            if not case_sensitive:
                ch_name_lower = ch_name.lower()
                topic_value_lower = topic_value.lower()
                purpose_value_lower = purpose_value.lower()
            else:
                ch_name_lower = ch_name
                topic_value_lower = topic_value
                purpose_value_lower = purpose_value
            
            # 매치 검사
            for kw in keywords:
                if (kw in ch_name_lower or 
                    kw in topic_value_lower or 
                    kw in purpose_value_lower):
                    filtered_channels.append(ch)
                    break
        
        # 필터링 결과 표시
        st.info(f"키워드 '{filter_input}'로 필터링 결과: {len(filtered_channels)}개 채널")
        
        channel_objs = filtered_channels

    # 채널 선택 및 요약 영역 - 한 행에 모든 영역 표시
    if len(channel_objs) > 0:
        st.markdown("## 🔍 채널 선택 및 요약")
        
        # 한 행에 3개 컬럼으로 배치
        col1, col2, col3 = st.columns([3, 1, 1])
        
        with col1:
            # 채널 선택 드롭다운
            channels = {}
            channel_display_lookup = {}  # 채널 이름 -> 화면에 표시되는 이름
            
            # 채널 이름에 최근 메시지 있음을 표시 (이모지 추가)
            for ch in channel_objs:
                channel_name = ch["name"]
                has_messages = ch.get("has_messages", False)
                # 최근 메시지가 있는 채널은 이모지로 표시
                display_name = f"📢 {channel_name}" if has_messages else f"💤 {channel_name}"
                channels[display_name] = ch["id"]
                channel_display_lookup[channel_name] = display_name
            
            # 체크박스에서 선택한 채널들을 기본 선택으로 설정
            default_selected = []
            for channel_name in st.session_state.selected_for_summary:
                display_name = channel_display_lookup.get(channel_name)
                if display_name:
                    default_selected.append(display_name)
            
            selected_channels_display = st.multiselect(
                "요약할 채널 선택",
                options=sorted(channels.keys()),
                default=default_selected
            )
            
            # 선택된 표시 이름에서 실제 채널 이름과 ID 추출
            selected_channels = []
            selected_channel_ids = {}
            for display_name in selected_channels_display:
                # 이모지 제거 (첫 3글자 제거: 이모지와 공백)
                real_name = display_name[3:]
                selected_channels.append(real_name)
                # 바로 ID 매핑도 저장
                selected_channel_ids[real_name] = channels[display_name]
            
            # 멀티셀렉트에서 선택된 채널들을 세션 상태에 반영 (동기화)
            st.session_state.selected_for_summary = set(selected_channels)
        
        with col2:
            # 최대 스레드 수 설정
            max_threads = st.number_input("채널당 최대 스레드 수", min_value=1, max_value=100, value=20)
        
        with col3:
            # 버튼을 드롭다운과 같은 높이에 배치
            st.markdown("<div style='padding-top: 30px;'></div>", unsafe_allow_html=True)
            start_summary = st.button("요약 시작", use_container_width=True, type="primary")
        
        # 구분선 추가
        if start_summary and selected_channels:
            st.markdown("<hr>", unsafe_allow_html=True)
            st.markdown("## 요약 결과")
            
            total_threads = 0
            empty_channels = []  # 스레드가 없는 채널을 추적하기 위한 리스트
            
            for name in selected_channels:
                # 변경된 UI에 맞게 ID 가져오기 방식 변경
                channel_id = selected_channel_ids.get(name)
                if not channel_id:
                    st.warning(f"채널 #{name} 접근 불가")
                    continue

                with st.spinner(f"#{name} 채널의 스레드 로드 중..."):
                    try:
                        response = client.conversations_history(channel=channel_id, oldest=since_ts)
                        messages = response["messages"]
                        threads = [m for m in messages if "thread_ts" in m and m["ts"] == m["thread_ts"]]
                        
                        if not threads:
                            # 스레드가 없는 채널 이름만 리스트에 추가하고 계속 진행
                            empty_channels.append(name)
                            continue
                            
                        # 최대 스레드 수 제한
                        threads = threads[:max_threads]
                        total_threads += len(threads)
                        
                        # 채널 제목
                        st.markdown(f"### {name} ({len(threads)}개 스레드)")
                        
                        # 상태 표시를 위한 placeholder 사용
                        status_placeholder = st.empty()

                        # 각 스레드를 카드 형태로 표시
                        for i, t in enumerate(threads):
                            # 기존 spinner 대신 상태 텍스트만 업데이트
                            status_placeholder.text(f"스레드 {i+1}/{len(threads)} 처리 중...")
                            
                            thread_ts = t["ts"]
                            thread_messages = fetch_thread_replies(channel_id, thread_ts)
                            summary = summarize_thread(thread_messages, user_map)
                            
                            # 타임스탬프를 보기 좋게 변환
                            dt_obj = datetime.fromtimestamp(float(thread_ts), tz=KST)
                            formatted_time = dt_obj.strftime("%Y-%m-%d %H:%M")
                            
                            # 스레드 URL 생성 - 항상 team_name 사용
                            team_name = getattr(st.session_state, 'team_name', '')
                            team_id = getattr(st.session_state, 'team_id', 'T0123456')
                            
                            # team_name이 있으면 사용, 없으면 기본값(workspace) 사용
                            if not team_name:
                                team_name = "workspace"
                                
                            thread_url = get_slack_thread_url(team_name, channel_id, thread_ts)
                            
                            # 디버깅 정보 표시
                            print(f"Thread 정보: team_id={team_id}, team_name={team_name}, channel_id={channel_id}, ts={thread_ts}")
                            
                            # HTML 이스케이프 적용하여 코드가 실행되지 않도록 함
                            import html
                            escaped_summary = html.escape(summary)
                            
                            # 심플한 카드 스타일로 표시
                            st.markdown(f"""
                            <div style="border: 1px solid #e6e6e6; padding: 15px; border-radius: 5px; margin-bottom: 15px; background-color: #f9f9f9;">
                                <div style="display: flex; justify-content: space-between; margin-bottom: 10px;">
                                    <span><strong>🕒 {formatted_time}</strong></span>
                                    <span><a href="{thread_url}" target="_blank" rel="noopener noreferrer" class="slack-link">스레드 바로가기 🔗</a></span>
                                </div>
                                <div style="padding: 15px; background-color: white; border-radius: 4px; margin-bottom: 10px; font-size: 16px; color: #333; line-height: 1.5;">
                                    {escaped_summary}
                                </div>
                                <div style="font-size: 12px; color: #666; text-align: right;">
                                    메시지 {len(thread_messages)}개
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
                        
                        # 모든 스레드 처리 완료 후 상태 표시 제거
                        status_placeholder.empty()
                        
                    except Exception as e:
                        st.error(f"#{name} 채널 처리 중 오류 발생: {str(e)}")
            
            # 요약 결과 표시 - 중복 메시지 방지
            if total_threads == 0:
                # 모든 채널에 스레드가 없는 경우에만 전체 메시지 표시
                st.warning("선택한 채널에서 요약할 스레드를 찾지 못했습니다.")
            else:
                # 스레드가 없는 채널이 있었다면 개별 메시지 표시
                if empty_channels:
                    channels_str = ", ".join([f"#{ch}" for ch in empty_channels])
                    st.info(f"다음 채널에서는 스레드를 찾을 수 없었습니다: {channels_str}")
                
                # 전체 요약 결과 표시
                st.success(f"총 {total_threads}개 스레드 요약 완료!")
    else:
        st.warning("표시할 채널이 없습니다. 필터링 조건을 변경해보세요.")