# 한국어 패치 저장소 개발·운영

## 원본과 패치의 분리

이 저장소는 원본을 재구성하는 정보와 자체 변경만 관리한다.
원본 저장소의 변경은 merge 대신 고정 버전 갱신과 패치 재적용으로 반영한다.

~~~text
Stronghold-Protocol/
  upstream.lock.json            원본 URL, 태그, 전체 커밋 SHA, Full Release SHA256
  patches/                      한국어 교정 JSON, 순서대로 적용하는 소스 패치
  overlays/                     원본에 추가할 자체 파일
  deploy/                       Compose·환경 설정·검사 이미지 템플릿
  scripts/                      Python 표준 라이브러리 구성 CLI
  tests/                        구성·충돌·실패 복구·비밀값 보존 검사
  .cache/upstream/source/       순수 원본 Git checkout (Git 제외)
  .build/Stronghold-Protocol/   패치 적용한 별도 Git checkout (Git 제외)
  service/                     비밀 .env, 생성 Compose, 로컬 에셋·복구 기록 (Git 제외)
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
python3 scripts/project.py prepare
python3 scripts/project.py assets
python3 scripts/project.py build
python3 scripts/project.py configure
python3 scripts/project.py up
python3 scripts/project.py verify
~~~

assets/build/configure는 setup으로 묶어 실행할 수 있다. setup은 기본적으로 구성과 빌드까지만 한다.

~~~bash
python3 scripts/project.py setup
python3 scripts/project.py setup --start
~~~

기존 service/.env의 Tunnel token, 알 수 없는 설정과 주석은 보존한다. 새 환경에서는
deploy/env.example을 기준으로 .env가 생성되며, 최초 서비스 시작 전에 service/.env의 token을 입력한다.
이미지 이름, 한국어 음성 설정과 경로는 configure가 생성한다. .env 권한은 600이다.
루트 .env는 service/.env로 연결하여 IDE에서 같은 파일을 사용할 수 있다.

service/compose.yaml은 생성 파일이다. 템플릿 변경은 deploy/compose.yaml에 하고 configure로 반영한다.
빌드 context는 .build/Stronghold-Protocol의 절대 경로이며, 운영 에셋은 읽기 전용으로 마운트한다.
운영 프로젝트 이름 stronghold, 127.0.0.1:3000 포트와 기존 Named Tunnel 연결을 유지한다.
로컬 접속은 http://localhost:3000/?lang=ko이다.

## 추가 한글화와 기타 수정

한국어 교정은 patches/ko-ui.json의 messages에 추가하거나 value를 수정한다.
각 항목의 base는 원본 문구, value는 적용할 한국어다. context가 포함된 msgid 키는 그대로 사용하고,
placeholder를 보존한다. meta는 수정하는 _meta 필드만 포함한다.

기존 항목은 value만 편집한다. base는 현재 표시된 교정문이 아니라 고정 원본의 번역값이므로 그대로 둔다.
새 항목의 base는 .cache/upstream/source/public/i18n/ko.json에서 가져온다.
이 파일은 UI 문구 교정을 담당한다. 오퍼레이터·스킬·아이템 등 게임 데이터의 공식 한국어 원문은
별도 data/i18n/ko.json에서 가져오므로, 해당 데이터까지 변경하려면 소스 패치가 필요하다.

JSON을 저장한 뒤 다음 명령으로 검사하고 서비스에 반영한다. 파일 편집이나 Git push만으로
실행 중인 웹 서비스의 번역이 바뀌지는 않는다.

~~~bash
python3 scripts/project.py prepare
python3 scripts/project.py check
python3 scripts/project.py build
python3 scripts/project.py configure
python3 scripts/project.py up
python3 scripts/project.py verify
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
python3 scripts/project.py capture --path server/index.js --name 0002-my-change.patch
python3 scripts/project.py prepare
~~~

기존 파일은 차이만 patches에 저장하고, 새 파일은 overlays의 같은 상대 경로에 저장한다.
소스 패치는 번호 순서로 적용하므로 다음 번호를 사용한다.
capture는 지정한 파일만 보존하며, 관계없는 수정이 남아 있으면 prepare가 중단한다.
생성 소스의 보존되지 않은 수정을 덮어쓸 때만 prepare --force를 명시적으로 사용한다.
원본에 overlay와 같은 파일이 추가되면 충돌로 중단하여 병합 여부를 검토한다.
다운로드 에셋, 환경 설정과 생성 manifest는 capture하지 않는다.

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
python3 scripts/project.py up --dev
python3 scripts/project.py verify --dev
python3 scripts/project.py status --dev
python3 scripts/project.py down --dev
~~~

개발 서버는 별도 프로젝트와 127.0.0.1:3100 포트를 사용한다.
up은 빌드를 수행하지 않으므로 변경한 소스를 적용할 때는 build와 configure를 먼저 실행한다.
소스 준비·Git push·검사만으로 실행 중 운영 컨테이너의 프로그램이 교체되지는 않는다.

## 원본 업데이트와 에셋

다음 vX.Y.Z는 실제 존재하는 공식 릴리스 태그로 바꾼다.
자체 변경을 먼저 커밋한 뒤 업데이트용 작업 브랜치를 만든다.

~~~bash
git switch -c update/upstream-vX.Y.Z
python3 scripts/project.py update --ref vX.Y.Z
python3 scripts/project.py assets
python3 scripts/project.py build
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

검증한 lock·패치·스크립트를 main에 합쳐 push한 다음 configure/up/verify로 운영에 적용한다.
원본 tag 이름은 lock에 기록하며, 이 자체 저장소에는 원본 tag를 가져올 필요가 없다.
자체 릴리스에는 ko/v0.2.1-r1 같은 이름을 사용할 수 있다.

## 상태, 종료와 복구

~~~bash
python3 scripts/project.py status
python3 scripts/project.py down
python3 scripts/project.py up
python3 scripts/project.py rollback
python3 scripts/project.py rollback --snapshot SNAPSHOT_ID
~~~

구성을 변경하거나 다른 이미지로 배포하기 전에 이전 이미지와 비밀 설정을 복구 기록으로 보관한다.
운영에 사용 중인 이미지 태그를 다시 빌드할 때도 덮어쓰기 전에 이전 이미지를 보관한다.
복구 파일은 service/rollback 아래에 600 권한으로 보관하며 token을 출력하지 않는다.
같은 이미지를 단순히 다시 시작하는 작업은 이전 배포의 복구 대상을 바꾸지 않는다.
rollback은 이전 운영 이미지·설정으로 되돌린다. 실패 시 pending 복구 기록을 보존한다.

분리 전 운영 버전 0.1.4의 이미지와 로컬 에셋은 archive/legacy-v0.1.4에도 남아 있다.
분리 전 Git 소스만 복원하려면 bundle에서 별도 작업 폴더를 만들 수 있다.

~~~bash
git clone archive/repository-before-isolation.bundle archive/restored-source
~~~
