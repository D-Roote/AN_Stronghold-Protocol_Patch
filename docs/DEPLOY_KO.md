# 한국어 패치 저장소 개발·운영

## 원본과 패치의 분리

이 저장소는 원본을 재구성하는 정보와 자체 변경만 관리한다.
원본 저장소의 변경은 merge 대신 고정 버전 갱신과 패치 재적용으로 반영한다.

~~~text
Stronghold-Protocol/
  upstream.lock.json            원본 URL, 태그, 전체 커밋 SHA, Full Release SHA256
  patches/                      한국어 교정 JSON, 숫자 순서와 영어 분류를 가진 소스 패치
  overlays/                     원본에 추가할 자체 파일
  deploy/                       Compose·환경 설정·검사 이미지 템플릿
  scripts/                      Python 표준 라이브러리 구성 CLI
  tests/                        구성·충돌·실패 복구·비밀값 보존 검사
  .cache/upstream/source/       순수 원본 Git checkout (Git 제외)
  .build/Stronghold-Protocol/   패치 적용한 별도 Git checkout (Git 제외)
  service/                     비밀 .env, 생성 Compose, 로컬 에셋·기존 복구 기록 (Git 제외)
  archive/                     이전 설치 및 분리 전 Git bundle (Git 제외)
~~~

현재 pin은 원본 v0.2.1이다. Git SHA와 Full Release SHA256은 각각 소스와 로컬 에셋의 기준이다.
다운로드한 런타임 전체를 생성 소스에 덮어쓰지 않는다.
원본 LICENSE/NOTICE를 유지하며, 게임 에셋은 저장소에 포함하지 않는다.

사용자 승인으로 기존 main 이력은 패치 파일만 포함한 새 이력으로 교체한다.
분리 전 Git refs는 archive/repository-before-isolation.bundle에 보관하고 SHA256과 bundle 검증을 기록한다.
이 bundle에는 당시 추적한 소스·커밋·태그가 있으며, Git 제외 .env와 에셋은 별도 운영 백업에 남는다.

## 준비와 서비스 구성

작업 폴더는 /home/user/Stronghold-Protocol이다. 어디에서든 scripts/project.py의 절대 경로로도 실행할 수 있다.
WSL Ubuntu/Linux, Python 3.10 이상, Git, Docker와 Compose를 사용한다. 호스트 Node.js는 필요하지 않다.

~~~bash
python3 scripts/project.py setup
~~~

setup 하나로 원본 다운로드와 pin 검증, 한국어 패치 적용, 로컬 에셋 추출,
최종 KR 이미지 빌드와 service/Compose 설정 생성을 완료한다. prepare를 먼저 실행할 필요가 없다.
반복 실행하면 패치 변경을 감지해 소스를 다시 준비하며, 기본적으로 서버는 시작하지 않는다.

기존 service/.env의 Tunnel token, 알 수 없는 설정과 주석은 보존한다. 새 환경에서는
deploy/env.example을 기준으로 .env가 생성되며, 최초 서비스 시작 전에 service/.env의 token을 입력한다.
이미지 이름, 한국어 음성 설정과 경로는 configure가 생성한다. .env 권한은 600이다.
루트 .env는 service/.env로 연결하여 IDE에서 같은 파일을 사용할 수 있다.

service/compose.yaml은 생성 파일이다. 템플릿 변경은 deploy/compose.yaml에 하고 setup으로 반영한다.
빌드 context는 .build/Stronghold-Protocol의 절대 경로이며, 운영 에셋은 읽기 전용으로 마운트한다.
운영 프로젝트 이름 stronghold, 127.0.0.1:3000 포트와 기존 Named Tunnel 연결을 유지한다.
로컬 접속은 http://localhost:3000/이다. 최초 접속은 한국어로 시작하며 URL의 ?lang= 값이나
기존 브라우저에 저장한 언어 선택이 있으면 그 값을 우선한다.

준비 후에는 service/에서 Docker Compose로 관리한다.

~~~bash
cd service
docker compose up -d
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
~~~

Compose의 build context도 준비한 소스를 가리키며, FETCH_ASSETS=1과 VOICE_LANG=kr을 사용한다.
따라서 Python 운영 명령 없이 이미지를 다시 빌드하고 컨테이너를 교체할 수 있다.

~~~bash
# service/ 안에서 준비된 소스를 다시 빌드하고 적용
docker compose build
docker compose up -d
~~~

restart는 기존 컨테이너를 다시 시작한다. 새 이미지나 환경 설정을 적용할 때는 up -d를 사용한다.
새 패치를 적용하거나 원본 pin을 갱신할 때는 프로젝트 루트에서 setup을 다시 실행한 뒤 up -d를 실행한다.

## 추가 한글화와 기타 수정

한국어 교정은 patches/ko-ui.json의 messages에 추가하거나 value를 수정한다.
자체 추가 기능의 새 문구는 additions의 중국어 msgid와 한국어 문자열 쌍으로 관리한다.
messages는 원본 번역의 base를 검사하며, additions는 원본에 같은 키가 새로 생기면 값 충돌을 검사한다.
각 항목의 base는 원본 문구, value는 적용할 한국어다. context가 포함된 msgid 키는 그대로 사용하고,
placeholder를 보존한다. meta는 수정하는 _meta 필드만 포함한다.

기존 항목은 value만 편집한다. base는 현재 표시된 교정문이 아니라 고정 원본의 번역값이므로 그대로 둔다.
새 항목의 base는 .cache/upstream/source/public/i18n/ko.json에서 가져온다.
이 파일은 UI 문구 교정을 담당한다. 오퍼레이터·스킬·아이템 등 게임 데이터의 공식 한국어 원문은
별도 data/i18n/ko.json에서 가져오므로, 해당 데이터까지 변경하려면 소스 패치가 필요하다.

JSON을 저장한 뒤 다음 명령으로 검사하고 서비스에 반영한다. 파일 편집이나 Git push만으로
실행 중인 웹 서비스의 번역이 바뀌지는 않는다.

~~~bash
python3 scripts/project.py setup
python3 scripts/project.py check
cd service
docker compose up -d
python3 ../scripts/project.py verify
~~~

한국어 선택은 제목 화면 또는 설정의 Language / 语言 메뉴에 있다.
접속할 때 /?lang=ko를 사용하면 한국어로 시작하며 이후 선택이 브라우저에 저장된다.

prepare는 원본 값이 base 또는 이미 교정된 value와 같을 때만 적용한다.
원본에서 문구가 바뀌거나 삭제되면 해당 키를 알리고 중단한다. 교정하지 않은 새 원본 키는 보존한다.
한국어 표현 기준은 공식 KR UI 표와 기존 번역 참고이며, 결정 단계는 커뮤니티 표현이다.
일부 기계번역이 남아 있어 machineTranslated 표시를 유지한다.

소스 코드를 수정할 때는 생성 소스에서 편집한 후 필요한 파일만 capture한다.

~~~bash
python3 scripts/project.py prepare
# .build/Stronghold-Protocol 안의 필요한 소스 파일 편집
python3 scripts/project.py capture --path server/index.js --name 0005-Feat-my-change.patch
python3 scripts/project.py prepare
~~~

기존 파일은 차이만 patches에 저장하고, 새 파일은 overlays의 같은 상대 경로에 저장한다.
소스 패치는 0001-Build-korean-voice.patch처럼 4자리 숫자, Build-/UI-/Feat-/Fix- 등 영어 분류,
영어 설명으로 이름을 짓는다. 숫자 오름차순으로 적용하며 별도 순서 파일은 사용하지 않는다.
capture에는 현재 가장 큰 번호에 1을 더한 번호를 지정한다. 새 파일만 overlay로 추가하면
소스 패치가 생성되지 않으므로 번호를 사용하지 않는다. 원본에 반영된 패치를 삭제해 번호가
비어도 나머지 파일을 다시 번호 매길 필요는 없다. 잘못된 이름과 중복 번호는 적용 전에 오류로 처리한다.
capture는 지정한 파일만 보존하며, 관계없는 수정이 남아 있으면 prepare가 중단한다.
생성 소스의 보존되지 않은 수정을 덮어쓸 때만 prepare --force를 명시적으로 사용한다.
원본에 overlay와 같은 파일이 추가되면 충돌로 중단하여 병합 여부를 검토한다.
다운로드 에셋, 환경 설정과 생성 manifest는 capture하지 않는다.

## 사용자 에셋 다운로드와 팀 채팅

처음 접속하면 에셋 사전 다운로드 동의를 묻는다. 현재 전체 대상은 약 567MB이며 이미지·모델·
KR 음성·로컬 폰트를 브라우저에 저장한다. 동의하면 게임을 이용하는 동안 두 파일씩 백그라운드로
다운로드한다. 선택은 해당 브라우저에 저장하며, 거절해도 필요할 때 에셋을 불러와 플레이할 수 있다.

최초 동의 팝업 이후에는 설정의 에셋 다운로드에서 진행률 확인, 일시정지·재개·
재시도·캐시 삭제를 할 수 있다. 다운로드한 에셋은 이후 요청에서 재사용하며, 앱 코드·게임 데이터·
API는 이 캐시에 넣지 않는다. 에셋 버전이 바뀌면 이전 캐시를 정리하고 새 버전으로 준비한다.
브라우저의 저장 공간 정리로 캐시가 삭제되면 다시 다운로드해야 한다. HTTPS 또는 localhost에서
브라우저 캐시 기능을 사용할 수 있다.

좌하단에 별도의 다운로드 버튼을 표시하지 않는다. 다운로드 창은 기존 Modal/Button과
폰트·색상 체계를 사용한다. 모바일 세로 화면의 회전 안내는 0004-UI-mobile-orientation-ko.patch에서
한국어로 교정하며 기존 회전 그래픽과 표시 조건을 사용한다.

협동 게임의 좌하단은 교류·채팅·> 순서다. 교류의 기존 이모티콘 기능을 유지하며,
>를 펼치면 설정·매뉴얼·전체화면 버튼이 세로로 나타난다. 채팅은 같은 방의 실제 팀원에게만
전달되며 관전자·다른 방·봇은 대상에 포함하지 않는다. 메시지는 200자까지, 전송 간격은 1초이고
화면에서 10초 후 사라진다. 연결이 끊긴 동안 쓴 메시지는 자동 전송하지 않는다.
서버·브라우저 저장소·전투 리플레이에 채팅 기록을 보관하지 않는다.

추가 기능 설계와 작업 기록은 .cache/feature-work/에 작성하며 Git에서 제외한다.

## 검사와 개발 서버

~~~bash
python3 -m unittest discover -s tests -v
python3 scripts/project.py check
python3 scripts/project.py check --full
~~~

check는 Docker로 의존성을 설치하고 strict 한국어 검사, 음성 검사 테스트와
lint/import/typecheck를 실행한다. --full은 원본 전체 테스트도 실행한다.
생성 소스에는 별도 Git 이력과 추적 index를 두어 원본 테스트·패키징 도구가 작동하게 한다.
CI도 원본을 지정한 커밋으로 재구성하고 위 검사를 수행한다.

~~~bash
cd service
docker compose -f compose.dev.yaml up -d
python3 ../scripts/project.py verify --dev
docker compose -f compose.dev.yaml ps
docker compose -f compose.dev.yaml down
~~~

개발 서버는 별도 프로젝트와 127.0.0.1:3100 포트를 사용한다.
setup에서 준비한 소스와 KR 이미지를 사용하며, 별도의 prepare나 configure는 필요하지 않다.
소스 준비·Git push·검사만으로 실행 중 운영 컨테이너의 프로그램이 교체되지는 않는다.

## 원본 업데이트와 에셋

다음 vX.Y.Z는 실제 존재하는 공식 릴리스 태그로 바꾼다.
자체 변경을 먼저 커밋한 뒤 업데이트용 작업 브랜치를 만든다.

~~~bash
git switch -c update/upstream-vX.Y.Z
python3 scripts/project.py update --ref vX.Y.Z
python3 scripts/project.py setup
python3 scripts/project.py check --full
~~~

update는 원본 태그를 조회하고 GitHub Full Release digest를 확인한다.
공개 digest가 없으면 별도로 검증한 --release-sha256 값을 지정한다.
한글화 가드나 소스 패치가 충돌하면 lock과 마지막 생성 소스를 보존하고 중단한다.
순수 원본과 변경 내역을 검토해 패치를 조정한 뒤 다시 실행한다.
업데이트는 운영 컨테이너를 교체하지 않는다.

로컬 에셋은 같은 Full Release의 public/assets/local와 data/local-assets.json만 함께 추출한다.
ZIP SHA256, 경로, manifest 항목 수와 모든 참조 파일을 확인한 뒤 버전별 폴더에 보관한다.
기존 운영 볼륨을 덮어쓰지 않고 configure에서 해당 버전 경로를 선택한다.
원본 0.1.3에서 0.2.1로는 소환물 39개·파일 117개가 추가되며, manifest는 1481에서 1598개가 된다.
한국어 음성은 별도의 KR 빌드를 사용하며, CN Full Release의 음성을 복사하지 않는다.

검증한 lock·패치·스크립트를 main에 합쳐 push한 다음 service/에서 docker compose up -d로 운영에 적용한다.
반영 후 보조 검사 python3 ../scripts/project.py verify를 사용할 수 있다.
원본 tag 이름은 lock에 기록하며, 이 자체 저장소에는 원본 tag를 가져올 필요가 없다.
자체 릴리스에는 ko/v0.2.1-r1 같은 이름을 사용할 수 있다.

## 상태와 종료

~~~bash
cd service
docker compose ps
docker compose down
docker compose up -d
~~~

기본 configure/build/up은 복구 이미지나 설정 백업을 생성하지 않는다.
배포에 필요하면 서버를 중단하거나 컨테이너를 재생성할 수 있으며, 이전 서버·이미지 유지는 전제하지 않는다.
현재 service/.env의 비밀값과 알 수 없는 설정은 계속 보존하고 실제 파일 권한은 600으로 유지한다.

Python의 prepare/assets/build/configure는 준비 단계의 부분 실행 도구이며,
check/verify와 up/down/status는 보조 명령으로 남아 있다. 일상 운영은 Docker Compose로 수행한다.

이미 만들어진 service/rollback 기록과 archive 백업은 남겨 두었다.
rollback 명령은 이 기존 기록을 사용할 때만 유효하며, 새 배포는 복구 대상을 자동 갱신하지 않는다.

~~~bash
python3 scripts/project.py rollback
python3 scripts/project.py rollback --snapshot SNAPSHOT_ID
~~~

분리 전 운영 버전 0.1.4의 이미지와 로컬 에셋은 archive/legacy-v0.1.4에도 남아 있다.
분리 전 Git 소스만 복원하려면 bundle에서 별도 작업 폴더를 만들 수 있다.

~~~bash
git clone archive/repository-before-isolation.bundle archive/restored-source
~~~
