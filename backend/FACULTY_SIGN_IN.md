# 교수자 Google 로그인 배포

1. Google Cloud에서 웹 OAuth 클라이언트를 만들고 승인된 JavaScript 원본에 `https://learning-x-one.vercel.app`을 등록합니다.
2. Northflank API 런타임에 `NIT_GOOGLE_CLIENT_ID`를 설정합니다.
3. `NIT_GOOGLE_FACULTY_ACCOUNTS`에는 Google 계정의 검증된 고유 ID(sub)를 기존 활성 교수자 사용자 ID에 대응시키는 JSON을 등록합니다. 예시 형태는 `{"google-sub":"existing-faculty-user-id"}`입니다. 기존 사용자 ID를 쓰면 수업과 교안의 소유권이 이어집니다.
4. `NIT_CORS_ORIGINS`에 운영 프런트 주소가 포함되어 있는지 확인합니다. API 배포 후 등록된 계정과 미등록 계정으로 각각 로그인 결과를 확인하고 프런트를 배포합니다.
5. 교수자가 교안을 승인하고 수업을 시작하면 학생용 코드가 자동으로 표시됩니다. 학생은 이 코드로 입장하며 수업 종료 후에는 코드가 만료됩니다.

Vercel의 계정 인증 설정은 그대로 유지됩니다. Google 클라이언트 비밀값은 이 흐름에서 필요하지 않습니다.
