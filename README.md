# Slack Channel Summarizer

Slack 채널의 대화 내용을 요약하고 분석하는 도구입니다.

## 기능

- Slack 채널 목록 조회 및 필터링
- 채널 대화 내용 요약
- 주요 토픽 추출
- 타임스탬프 기반 대화 필터링

## 설치 방법

1. 레포지토리 클론:
```bash
git clone https://github.com/[YOUR_USERNAME]/slack-ai-chatbot.git
cd slack-ai-chatbot
```

2. 가상환경 생성 및 활성화:
```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
```

3. 의존성 설치:
```bash
pip install -r requirements.txt
```

4. 환경 변수 설정:
- `.env-example` 파일을 `.env`로 복사하고 필요한 값들을 설정합니다.
- 필요한 환경 변수:
  - `SLACK_BOT_TOKEN`
  - `SLACK_USER_TOKEN`
  - `OPENAI_API_KEY`

## 실행 방법

```bash
streamlit run main.py
```

## 라이선스

MIT License

## 기여 방법

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request 