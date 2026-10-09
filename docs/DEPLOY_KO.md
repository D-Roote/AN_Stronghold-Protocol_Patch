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

현재 pin은 원본 v0.2.3이다. Git SHA와 Full Release SHA256은 각각 소스와 로컬 에셋의 기준이다.
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
최종 KR/JP 음성 이미지 빌드와 service/Compose 설정 생성을 완료한다. prepare를 먼저 실행할 필요가 없다.
반복 실행하면 패치 변경을 감지해 소스를 다시 준비하며, 기본적으로 서버는 시작하지 않는다.

기존 service/.env의 Tunnel token, 알 수 없는 설정과 주석은 보존한다. 새 환경에서는
deploy/env.example을 기준으로 .env가 생성되며, Cloudflare Tunnel을 선택하면 최초 서비스 시작 전에 service/.env의 token을 입력한다.
nginx 구성은 token을 사용하지 않는다.
이미지 이름, 한국어 음성 설정과 경로는 configure가 생성한다. .env 권한은 600이다.
루트 .env는 service/.env로 연결하여 IDE에서 같은 파일을 사용할 수 있다.

service/의 stack.*.yaml과 nginx*.conf 및 *.template는 생성 파일이다. 템플릿은 deploy/에서 수정하고 setup으로 반영한다.
기본 이름의 compose.yaml은 생성하지 않으므로 항상 -f로 실행할 구성을 선택한다.
이전 생성본과 동일한 compose.yaml/compose.dev.yaml은 configure가 제거한다.
사용자가 수정한 이전 파일은 덮어쓰지 않고 중단하므로 다른 파일명으로 옮긴 뒤 재실행한다.
빌드 context는 .build/Stronghold-Protocol의 절대 경로이며, 운영 에셋은 읽기 전용으로 마운트한다.
운영 프로젝트 이름 stronghold, 127.0.0.1:3000 포트와 기존 Named Tunnel 연결을 유지한다.
로컬 접속은 http://localhost:3000/이다. 최초 접속은 한국어로 시작하며 URL의 ?lang= 값이나
기존 브라우저에 저장한 언어 선택이 있으면 그 값을 우선한다.

설정의 **오퍼레이터 음성 언어**에서 한국어·일본어를 선택한다. 한국어가 기본이며 선택값은
브라우저에 저장한다. UI 언어와 별개로 적용하며, 변경하면 재생·디코딩 중인 이전 음성을
취소하고 다음 음성부터 선택한 언어를 사용한다. 해당 언어의 음성이 없는 오퍼레이터는 무음이다.
setup과 Compose 빌드는 두 음성팩을 함께 포함하며 verify는 KR·JP의 경로와 파일을 모두 검사한다.
VOICE_LANG=kr은 원본 호환용 기본 음성 은행을 지정하는 빌드값이며 브라우저 음성 선택과 별개다.
0.2.3의 오퍼레이터 상세 화면에서는 개별 음성을 한국어·일본어·전역 설정 따르기로 선택할 수 있다.
이 설정은 원본의 `settings.voiceOverrides`로 저장하며 편성·서버 설정에는 포함하지 않는다.

준비 후에는 service/에서 Docker Compose로 관리한다.

~~~bash
cd service
docker compose -f stack.cf-tunnel.yaml up -d
docker compose -f stack.cf-tunnel.yaml ps
docker compose -f stack.cf-tunnel.yaml logs -f
docker compose -f stack.cf-tunnel.yaml restart
docker compose -f stack.cf-tunnel.yaml down
~~~

Compose의 build context도 준비한 소스를 가리키며, FETCH_ASSETS=1과 VOICE_LANG=kr을 사용한다.
따라서 Python 운영 명령 없이 이미지를 다시 빌드하고 컨테이너를 교체할 수 있다.

~~~bash
# service/ 안에서 준비된 소스를 다시 빌드하고 적용
docker compose -f stack.cf-tunnel.yaml build
docker compose -f stack.cf-tunnel.yaml up -d
~~~

restart는 기존 컨테이너를 다시 시작한다. 새 이미지나 환경 설정을 적용할 때는 up -d를 사용한다.
새 패치를 적용하거나 원본 pin을 갱신할 때는 프로젝트 루트에서 setup을 다시 실행한 뒤 up -d를 실행한다.

## 게이트웨이 선택과 Ubuntu 서버 이전

| 파일 | 실행 구성 | 접속 |
| --- | --- | --- |
| stack.cf-tunnel.yaml | 앱 + 기존 Cloudflare Named Tunnel | Tunnel 도메인 또는 localhost:3000 |
| stack.nginx.yaml | 앱 + nginx 역방향 프록시 | 기본 HTTP 80 |
| stack.dev.yaml | 별도 개발 앱 | localhost:3100 |

위 세 파일은 각각 독립된 실행 구성이므로 함께 병합하지 않는다. 운영 두 구성은 동일한 stronghold
프로젝트와 앱 포트를 사용한다. 전환할 때 현재 게이트웨이를 먼저 종료한다.

~~~bash
cd service
# Tunnel을 종료한 뒤 nginx를 선택하는 예
docker compose -f stack.cf-tunnel.yaml down
docker compose -f stack.nginx.yaml up -d
docker compose -f stack.nginx.yaml ps
python3 ../scripts/project.py verify --gateway nginx
~~~

nginx는 Docker 내부의 stronghold:3000으로 HTTP와 WebSocket을 전달한다.
[nginx WebSocket 문서](https://nginx.org/en/docs/http/websocket.html)에 따라 Upgrade 헤더를 전달한다.
service/.env의 NGINX_BIND_IP(기본 0.0.0.0), NGINX_HTTP_PORT(기본 80)로 공개 주소와 포트를 지정한다.
nginx.conf는 기본 HTTP 구성이다. 외부 접속의 HTTPS는 아래의 `scripts/tls.py`와
`stack.nginx-acme.yaml`로 인증서 발급 및 자동 갱신까지 구성할 수 있다.
브라우저 에셋 사전 다운로드 캐시는 HTTPS 또는 localhost에서 사용할 수 있다.
서버 이전 시 SSH·방화벽·OCI 네트워크의 포트 공개는 해당 서버에서 설정한다.

Ubuntu 2코어/12GB 구성을 고려해 앱의 기존 메모리 제한 4GB를 유지하며,
nginx는 워커 1개, 메모리 128MB와 CPU 0.25로 제한한다. 모든 구성은 SP_MAX_BOTS=1을 적용한다.
협동 방마다 추가 AI 팀원은 최대 1명이며 UI에서 추가 버튼을 비활성화하고 서버에서도 초과 요청을 거부한다.
단순 Node 실행의 기본값은 원본을 유지하므로 직접 실행할 때에도 SP_MAX_BOTS=1을 지정한다.
이 제한은 방별 제한이므로 여러 방을 동시에 운영할 때의 전체 부하는 별도로 확인한다.

새 서버에는 이 패치 저장소와 비공개 service/.env를 준비한 뒤 setup을 실행하여
해당 서버 아키텍처의 이미지를 빌드한다. 생성 소스와 service 에셋 경로는 새 환경에서 자동 구성된다.
기존 PC의 절대 경로를 담은 생성 Compose 파일을 복사하는 대신 새 서버의 deploy 템플릿을 사용한다.
현재 작업은 이전용 구성을 준비하는 범위이며 실제 Oracle VM 이전은 수행하지 않았다.

## Let's Encrypt HTTPS 자동 구성

Cloudflare를 DNS 전용으로 사용하면 nginx가 HTTPS/WSS를 직접 처리한다.
인증서 발급에는 Alpine 기반 공식 acme.sh 3.1.6 이미지를 사용한다. AMD64·ARM64를 지원하며
컨테이너 메모리는 128MB, CPU는 0.25로 제한한다. Let's Encrypt를 명시적으로 선택한다.
호스트에 ACME 클라이언트나 cron을 설치하지 않는다.

먼저 게임/에셋 도메인의 A 레코드를 해당 서버 공인 IP에 연결한다. AAAA 레코드가 있으면
IPv6 경로도 인증 요청을 처리해야 하므로 사용하지 않는 이전 AAAA는 제거한다.
OCI 네트워크 규칙과 서버 방화벽에서 TCP **80·443**을 허용한다. 홈서버는 공유기 포트포워딩도
필요하다. [HTTP-01 인증은 외부 80 포트로 접속](https://letsencrypt.org/docs/challenge-types/#http-01-challenge)하므로
갱신 중에도 계속 열어 둔다. HTTPS는 외부 443으로 연결한다. 기존 Tunnel을 이용한 연결은 종료한다.

### 게임 서버

~~~bash
python3 scripts/project.py setup
python3 scripts/tls.py configure --domain game.example.com --email admin@example.com
python3 scripts/tls.py issue
cd service
docker compose -f stack.nginx.yaml -f stack.nginx-acme.yaml ps
~~~

도메인·이메일은 실제 값으로 바꾼다. `configure`는 설정만 작성하며 인증서 발급이나 컨테이너 시작을
하지 않는다. 기존 `.env`의 비밀값, 소스·에셋 경로를 보존한다. `issue`는 nginx의 인증용 HTTP를 시작하고
인증서를 발급·설치한 다음 HTTPS를 활성화하고 갱신 컨테이너를 시작한다. 처음 발급하기 전에는
인증 경로와 헬스 체크만 HTTP로 제공하며, 발급 후 일반 HTTP 접속은 HTTPS로 308 리다이렉트한다.
인증서 저장 디렉터리가 아닌 개별 PEM 파일을 bind mount하지 않으므로 갱신된 파일도 바로 읽을 수 있다.

운영은 반드시 두 파일을 함께 사용한다.

~~~bash
cd service
docker compose -f stack.nginx.yaml -f stack.nginx-acme.yaml up -d --wait
docker compose -f stack.nginx.yaml -f stack.nginx-acme.yaml logs --tail=50 nginx acme
docker compose -f stack.nginx.yaml -f stack.nginx-acme.yaml down
~~~

`setup`을 다시 실행해도 TLS 설정과 인증서는 유지된다. 이후 위의 `up`으로 새 앱 이미지를 적용한다.
`project.py up/down/status/verify --gateway nginx`도 `.env`의 TLS 구성을 인식해 두 파일을 함께 사용한다.
인증서는 이미 발급되어 있으면 재사용하므로 컨테이너 재생성 때 발급을 반복하지 않는다.

### 분리한 홈 에셋 서버

`~/SP_Assets/.env`에 `ASSET_BUNDLE_DIR=./assets` 등 완전한 번들 경로가 준비되어 있으면
작업 저장소의 스크립트로 구성 파일을 배포한다. 기존 에셋 서버를 종료한 다음 실행한다.

~~~bash
cd ~/Stronghold-Protocol
python3 scripts/tls.py --directory ~/SP_Assets configure \
  --target assets --domain assets.example.com --email admin@example.com
python3 scripts/tls.py --directory ~/SP_Assets issue
cd ~/SP_Assets
docker compose -f stack.assets-direct.yaml -f stack.assets-acme.yaml ps
~~~

이 구성은 에셋 서버의 HTTP 포트를 **8081에서 80으로**, HTTPS 포트를 443으로 설정한다.
공유기에서 외부 80·443을 해당 포트로 전달한다. 별도 프록시 때문에 내부 포트를 바꿔야 한다면
`configure` 후 `.env`의 `ASSET_HTTP_PORT`/`ASSET_HTTPS_PORT`를 바꾸되 외부 포트는 80·443을 유지한다.
`ASSET_PUBLIC_PATH`는 그대로 보존한다. 인증 경로 `/.well-known/acme-challenge/`에는 이 prefix를 붙이지 않는다.
`stack.assets-https.yaml`은 수동 인증서용이므로 자동 구성과 함께 병합하지 않는다.
이 TLS 구성은 에셋 우선 서버 선택·게임 서버 fallback 기능을 추가하지 않는다.

### 갱신과 확인

공식 [acme.sh Docker daemon](https://github.com/acmesh-official/acme.sh/wiki/Run-acme.sh-in-docker)이
주기적으로 갱신 필요 여부를 확인한다. 고정 인증서 수명을 가정하지 않고 클라이언트의 갱신 판단을 따른다.
완료 후 인증서·키의 일치, 도메인과 만료일을 검증하고 nginx에 공유하는 파일을 교체한다.
nginx는 5초 안에 변경을 감지해 설정 검증 후 재로드한다. 잘못된 인증서·키는 활성 인증서를 덮어쓰지 않는다.
Docker socket은 공유하지 않으며 nginx에는 인증서 디렉터리를 읽기 전용으로 마운트한다.
[nginx 재로드](https://nginx.org/en/docs/control.html#reconfiguration)는 기존 연결을 유지하므로 게임 앱을 재시작할 필요가 없다.

~~~bash
# 필요할 때만 갱신 여부를 즉시 확인; 강제 발급하지 않음
python3 scripts/tls.py renew
# 분리 에셋 서버
python3 scripts/tls.py --directory ~/SP_Assets renew
curl -f https://game.example.com/healthz
curl -f https://assets.example.com/healthz/assets
~~~

비공개 `.env`에는 `TLS_DOMAIN`, `TLS_EMAIL`, `TLS_CA`, `TLS_STATE_DIR`, `TLS_UID/GID`가 기록된다.
계정과 키는 운영 디렉터리의 `tls/production/<도메인>/` 아래에 보관하고 Git에 포함하지 않는다.
도메인을 바꿔 `configure`하면 별도 상태를 사용하므로 이전 도메인의 인증서를 재사용하지 않는다.
다른 런타임 디렉터리로 옮기면 그 경로에서 `configure`를 다시 실행한다. 이미지 변경은
`.env`의 `TLS_ACME_IMAGE`로 지정하고 Compose `pull acme` 후 `up -d`로 적용할 수 있다.

테스트 발급은 `configure`에 `--staging`을 추가한다. 이 인증서는 브라우저가 신뢰하지 않으며
`tls/staging/`의 별도 상태를 사용한다. 실제 서비스로 전환할 때 같은 `configure`를 `--staging` 없이
실행한 다음 `issue`를 실행한다. 반복적인 production 강제 발급은 하지 않는다.

발급 실패 시 DNS, 80 포트와 HTTP 인증 경로를 먼저 확인한다. 상세 ACME 로그는
`TLS_STATE_DIR/acme/acme.log`와 Compose `logs acme`에서 확인한다. 오류를 해결한 뒤 `issue`를 다시 실행한다.
운영 인증서가 있는 경우 발급 시도가 실패해도 기존 HTTPS를 유지하며 자동 갱신 컨테이너를 다시 시작한다.

## 직접 nginx 에셋 서버

게임 서버와 별도인 `stronghold-assets` 프로젝트로 nginx만 실행한다. CF Tunnel과 Node 서버는
필요하지 않으며 `stack.assets-direct.yaml`을 사용한다. `setup --asset-server`는 일반 준비 작업 뒤에
이미지의 `public/assets`, `public/fonts`와 같은 버전의 검증된 로컬 에셋을 완전한 번들로 추출한다.
한국어·일본어 음성도 포함된다. 이미지 추출용 컨테이너는 시작하지 않고 작업 후 제거한다.
모든 manifest 참조와 파일 해시를 확인한 후 교체하며 복구본을 남기지 않는다.

~~~bash
# 최초 준비: 게임과 에셋 서버 구성을 준비하지만 서버는 시작하지 않음
python3 scripts/project.py setup --asset-server

# 이미지를 이미 준비한 경우에는 추출만 실행
python3 scripts/project.py assets-export

cd service
docker compose -f stack.assets-direct.yaml up -d --wait
docker compose -f stack.assets-direct.yaml ps
docker compose -f stack.assets-direct.yaml logs -f
~~~

기본 번들 경로는 `service/assets/direct/current`다. 해당 경로와 모든 추출 파일은 Git 제외다.
`assets-export --output /절대/경로` 또는 `service/.env`의 `ASSET_BUNDLE_DIR`로 바꿀 수 있다.
상대 번들 경로는 service/ 기준이며, 추출 후 실제 절대 경로를 .env에 기록한다.
같은 이미지와 파일을 재사용할 때는 추출을 생략한다. 일반 사용자 디렉터리를 덮어쓰지 않는다.

| service/.env 설정 | 의미와 기본값 |
| --- | --- |
| ASSET_BUNDLE_DIR | 검증된 전체 번들 경로, 추출 스크립트가 기록 |
| ASSET_BIND_IP | nginx 바인딩 주소, `0.0.0.0` |
| ASSET_HTTP_PORT | HTTP 포트, `8081` |
| ASSET_PUBLIC_PATH | URL 앞 경로, 기본 빈 값. 예: `/stronghold` |
| ASSET_HTTPS_PORT | 직접 TLS 포트, `443` |
| ASSET_TLS_CERT | 전체 인증서 체인 PEM 파일 경로 |
| ASSET_TLS_KEY | 해당 인증서의 개인 키 PEM 파일 경로 |

`ASSET_PUBLIC_PATH`는 영문·숫자·`_`·`-`로 구성한 경로를 사용하고 끝 `/`는 붙이지 않는다.
예를 들어 `/stronghold`면 `/stronghold/assets/...`, `/stronghold/fonts/...`,
`/stronghold/media/...`, `/stronghold/healthz/assets`로 제공한다. 도메인은 nginx에 고정하지 않는다.
`/media/`는 게임과 같은 확장자 없는 음성 요청을 지원하며 Range/206·ETag·CORS를 제공한다.
게임 HTML, JavaScript, 데이터 API 및 WebSocket 경로는 제공하지 않는다.
헬스 응답은 `ok`, 앱 버전 및 번들 ID를 포함하고 캐시하지 않는다.

현재 다운로드 클라이언트는 게임 서버 주소를 사용한다. 이 Compose를 실행하거나 DNS만 변경해도
별도 에셋 서버를 자동 선택하지는 않는다. 브라우저의 우선 서버 선택·장애 시 게임 서버 재시도는
별도 다운로드 라우팅 작업이 필요하다. 이 구성은 그 기능에서 사용할 수 있는 정적 서버를 준비한다.

기존 HTTPS 역방향 프록시를 사용한다면 HTTP 8081로 전달한다. 홈서버 nginx에서 직접 TLS를
종료하려면 인증서 파일을 준비해 .env의 인증서 경로를 지정하고 다음 두 파일을 함께 사용한다.
[nginx TLS 설정](https://nginx.org/en/docs/http/ngx_http_ssl_module.html)을 따른다.

~~~bash
cd service
docker compose -f stack.assets-direct.yaml -f stack.assets-https.yaml up -d --wait
~~~

TLS 구성은 HTTP의 에셋 요청을 HTTPS로 이동시키며 HTTP 헬스 체크는 유지한다.
DNS·포트 포워딩·인증서 발급 및 갱신은 사용 환경에서 설정한다. 인증서 파일이 교체되면 같은 Compose로
`up -d --force-recreate --wait`를 실행해 새 파일을 마운트한다. HTTPS 게임의 브라우저에서 이용하려면 에셋 서버도
HTTPS 주소로 제공한다.

패치나 원본을 업데이트하면 `setup --asset-server` 또는 이미지 빌드 후 `assets-export`를 다시 실행한다.
번들 디렉터리를 교체하므로 실행 중 에셋 서버는 다음 명령으로 마운트를 새로 연결한다.

~~~bash
cd service
docker compose -f stack.assets-direct.yaml up -d --force-recreate --wait
# 직접 TLS 구성이라면 위 명령에도 -f stack.assets-https.yaml을 추가
~~~

## 추가 한글화와 기타 수정

한국어 교정은 patches/ko-ui.json의 messages에 추가하거나 value를 수정한다.
자체 추가 기능의 새 문구는 patches/ko-features.json의 additions에 중국어 msgid와 한국어 문자열
쌍으로 관리한다. prepare는 ko-ui.json을 먼저 적용한 뒤 ko-features.json을 적용한다.
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
docker compose -f stack.cf-tunnel.yaml up -d
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
python3 scripts/project.py capture --path server/index.js --name 03-007-Feat-my-change.patch
python3 scripts/project.py prepare
~~~

기존 파일은 차이만 patches에 저장하고, 새 파일은 overlays의 같은 상대 경로에 저장한다.
소스 패치는 01-001-Build-korean-voice.patch처럼 분류 번호 2자리, 분류 내 번호 3자리,
영어 분류와 설명으로 이름을 짓는다. 분류는 01 Build, 02 UI, 03 Feat, 04 Resource, 05 Fix이다.
분류 번호 → 분류 내 번호 순서로 적용하며 별도 순서 파일은 사용하지 않는다.
현재 순서는 관련 기능을 모아 다음과 같이 적용한다.

| 순서 | 패치 | 내용 |
| --- | --- | --- |
| 01-001 | Build-korean-voice | KR 음성 빌드 |
| 01-002 | Build-japanese-voice-pack | KR/JP 음성팩 동시 준비와 검사 |
| 02-001 | UI-korean-first-visit | 최초 한국어와 언어 검사 |
| 02-002 | UI-mobile-orientation-ko | 모바일 회전 안내 |
| 02-003 | UI-title-controls | 이름 화면 가운데 상태·설명, 우하단 통계·설정·전체화면 |
| 02-004 | UI-compact-reroll-vote | 원본 투표 로직에 기존 버튼 디자인·문구, 준비 완료 줄 배치 |
| 02-900 | UI-draft-order-overflow | 선택 순서의 불필요한 가로 스크롤과 좁은 화면 문구 넘침 보정 |
| 02-910 | UI-korean-data-typo.json | 독립·멀티 초월 시뮬레이션 설명의 `강도하` → `강도가` |
| 03-001 | Feat-asset-prefetch | 에셋 사전 다운로드 |
| 03-002 | Feat-team-chat | 기본 채팅과 접이식 메뉴 |
| 03-003 | Feat-session-chat-factions | 세션 채팅·진영·전략 선택 UI |
| 03-004 | Feat-voice-language | 원본 전역·오퍼레이터별 음성 설정에 KR/JP 통합, 기존 선택값 이관 |
| 03-005 | Feat-briefing-restart-vote | **적용 제외·파일 보존**: 이전 자체 재시작 투표 구현 |
| 03-006 | Feat-upstream-reroll-integration | 원본 재시작 투표에 정보 확인 채팅·알림·검은 화면 전환 연결 |
| 04-001 | Resource-ai-teammate-limit | 방별 추가 AI 제한 |

capture에는 해당 분류의 가장 큰 번호에 1을 더한 번호를 지정한다. 새 분류는 001부터 시작한다.
앞 분류에 패치를 추가할 때는 전체 패치를 다시 적용해 후속 분류와의 의존성과 결과를 검증한다.
충돌하면 새 패치를 남기지 않고 생성 소스의 편집 내용을 보존한다. 다른 분류의 번호는 변경하지 않는다. 새 파일만 overlay로 추가하면
소스 패치가 생성되지 않으므로 번호를 사용하지 않는다. 원본에 반영된 패치를 삭제해 번호가
비어도 나머지 파일을 다시 번호 매길 필요는 없다. 잘못된 이름과 중복 번호는 적용 전에 오류로 처리한다.
02-900은 원본 레이아웃의 임시 보정을 UI 분류의 뒤쪽에 둔 패치다. 원본이 이 문제를 수정하면
제거를 검토한다. 채팅의 목록 아래 배치와 스크롤 영역 분리는 별도 기능 패치·overlay에 유지한다.
capture는 지정한 파일만 보존하며, 관계없는 수정이 남아 있으면 prepare가 중단한다.
생성 소스의 보존되지 않은 수정을 덮어쓸 때만 prepare --force를 명시적으로 사용한다.
원본에 overlay와 같은 파일이 추가되면 충돌로 중단하여 병합 여부를 검토한다.
다운로드 에셋, 환경 설정과 생성 manifest는 capture하지 않는다.

게임 데이터의 한국어 문구는 `02-910-UI-korean-data-typo.json`처럼 번호를 붙인 JSON 패치로 교정한다.
일반 `.patch`와 같은 숫자 순서로 적용하며 두 형식의 번호가 겹치면 중단한다. 이 형식은
`data/i18n/ko.json`의 지정된 문자열만 교정하고 원본 데이터 파일 전체를 저장소에 포함하지 않는다.
각 `changes` 항목에 필드 경로 `path`, 원본 문구 `base`, 수정 문구 `value`를 지정한다.
prepare에서 원본 문구가 바뀌거나 경로가 없어지면 기존 생성 소스를 보존하고 중단하며,
이미 수정 문구와 같으면 그대로 사용한다. 일반 소스 변경의 capture는 계속 `.patch`를 사용한다.

`patches/upstream-adoptions.json`은 원본 기능으로 전환한 패치와 overlay를 삭제하지 않고 적용에서만 제외한다.
0.2.3에서는 03-005 패치와 이전 재시작 서버·UI·테스트 파일 4개를 보존한다. 원본 재시작 엔진과
03-006 연결 패치가 활성 기능을 제공하므로 정보 확인 채팅과 알림·전환 효과를 유지한다.
적용 제외 목록의 태그·커밋, 보존 파일과 원본 대체 파일을 검증하며 새 원본 버전으로 이동할 때는 이 목록도
검토해야 한다. 잘못된 제외 경로나 대체 파일 누락은 소스 교체 전에 중단한다.

## 사용자 에셋 다운로드와 팀 채팅

처음 접속하면 에셋 사전 다운로드 동의를 묻는다. 이미지·모델·KR/JP 음성·로컬 폰트를
브라우저에 저장하며 설정에 전체 예상 용량을 표시한다. 동의하면 게임을 이용하는 동안 두 파일씩 백그라운드로
다운로드한다. 선택은 해당 브라우저에 저장하며, 거절해도 필요할 때 에셋을 불러와 플레이할 수 있다.

최초 동의 팝업 이후에는 설정의 에셋 다운로드에서 진행률 확인, 일시정지·재개·
재시도·캐시 삭제를 할 수 있다. 다운로드한 에셋은 이후 요청에서 재사용하며, 앱 코드·게임 데이터·
API는 이 캐시에 넣지 않는다. 에셋 버전이 바뀌면 이전 캐시를 정리하고 새 버전으로 준비한다.
브라우저의 저장 공간 정리로 캐시가 삭제되면 다시 다운로드해야 한다. HTTPS 또는 localhost에서
브라우저 캐시 기능을 사용할 수 있다.

좌하단에 별도의 다운로드 버튼을 표시하지 않는다. 다운로드 창은 기존 Modal/Button과
폰트·색상 체계를 사용한다. 모바일 세로 화면의 회전 안내는 02-002-UI-mobile-orientation-ko.patch에서
한국어로 교정하며 기존 회전 그래픽과 표시 조건을 사용한다.

협동 게임의 좌하단은 교류·채팅·> 순서다. 교류의 기존 이모티콘 기능을 유지하며,
>를 펼치면 설정·매뉴얼·전체화면 버튼이 오른쪽으로 나타난다. 협동 파티 대기실에서도 좌하단의
채팅 버튼을 사용할 수 있다. 정보 확인 단계에도 좌하단에 채팅 버튼을 표시하며,
전략 선택에서는 선택 순서의 스크롤 목록과 하단 안내 문구 사이에 표시한다.
같은 방의 대기실 → 정보 확인 → 전략 선택 → 전투에서 기록을 이어서 표시한다.
채팅은 같은 방의 실제 팀원에게만 전달되며 관전자·다른 방·봇은 대상에 포함하지 않는다.
메시지는 200자까지, 전송 간격은 1초다. 열린 채팅은 최근 50건을 페이지 메모리에 보존하고
스크롤한다. 기록 영역은 데스크톱에서 화면 높이의 절반, 모바일에서는 사용 가능한 세로 공간으로
제한한다. 다른 입력 필드나 설정 창을 사용하지 않을 때 Enter로 채팅을 열고 메시지를 전송할 수 있다.
빈 입력에서 Enter를 다시 누르면 입력창을 닫는다. 입력이 비어 있고 채팅을 조작하지 않은 채
5초가 지나도 자동으로 닫는다. 작성 중인 초안·IME 조합·진영 선택·전송 중에는 자동으로 닫지 않는다.
닫힌 기록 미리보기는 수신 후 5초가 지나지 않은 새 메시지만 최대 4건 표시한다.
각 메시지는 수신 후 5초에 개별적으로 숨기며, 새 메시지 수신이나 채팅창을 다시 닫아도
이미 숨겨진 기록을 재노출하지 않는다. 다시 열면 보존된 전체 기록을 확인할 수 있다.
방을 떠나거나 페이지를 새로 고치면 기록을 지운다.
연결이 끊긴 동안 쓴 메시지는 자동 전송하지 않는다. 서버·브라우저 영구 저장소·전투 리플레이에
채팅 기록을 보관하지 않는다.

입력창 오른쪽의 이모티콘·진영 버튼에서 목표 핵심 맹약을 선택한다. 염국·사르곤·빅토리아·
쉐라그·라테라노·에기르·시라쿠사·카시미어 8종을 지원하며, 선택 알림을 팀 채팅에 표시한다.
채팅과 대기실 닉네임 옆에 진영 이름과 색상을 표시한다. 진영 변경도 메시지 전송과 같은 1초
제한을 적용한다. 상세 이모티콘 선택은 추후 추가한다.

추가 기능 설계와 작업 기록은 .cache/feature-work/에 작성하며 Git에서 제외한다.

정보 확인 단계의 재시작 투표는 0.2.3의 원본 서버 프로토콜과 투표 상태를 사용한다.
방장이 시작하고, 현재 사람 팀원 모두의 동의가 필요하다. AI·관전자는 투표와 분모에서 제외한다.
화면 공간을 위해 별도 안내 행을 없애고 하단을 `오퍼레이터 설정 · 재시작 투표 / 준비 현황 · 준비 완료`
순서로 배치한다. 두 그룹 사이 여백은 그룹 내부 여백의 두 배이며, 좁은 화면에서는 그룹 단위로
줄을 바꿔 오퍼레이터 설정·재시작 투표와 준비 현황·준비 완료가 각각 같은 줄을 유지한다.
투표에는 기존 디자인의 `재시작 투표` / `재시작 n / 전체` 버튼을 표시한다.
새로고침 기호는 원본을 사용하고 찬성하면 테두리가 초록색이 된다.
방장은 다시 누르면 투표를 취소하고, 찬성한 팀원은 다시 누르면 원본의 거절 처리로 투표를 취소한다.
아직 찬성하지 않은 팀원은 작은 `×` 버튼으로 즉시 거절할 수 있다. 개인별 찬성 현황은 버튼의 도움말에 표시한다.
투표 중 정보 확인 타이머는 멈춘다.
사람 팀원 전원이 연결되어 있어야 시작할 수 있고 연결이 끊기거나 거절하면 원본 규칙대로 투표를 취소한다.
최초 요청은 기존 채팅에 초록색으로 한 번 알린다. 전원 동의 후 전장·보스·특훈 적·금지를 다시 추첨하여
정보 확인으로 돌아갈 때 짧은 검은 화면 전환을 표시한다. 방·닉네임·편성·채팅과 AI 선택 순서는 유지한다.
원본은 독립적인 시드로 다시 추첨하므로 결과가 우연히 같을 수 있다. 이전 자체 투표 구현 파일은 검토용으로
보존하며 동시에 구동하지 않는다.

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
docker compose -f stack.dev.yaml up -d
python3 ../scripts/project.py verify --dev
docker compose -f stack.dev.yaml ps
docker compose -f stack.dev.yaml down
~~~

개발 서버는 별도 프로젝트와 127.0.0.1:3100 포트를 사용한다.
setup에서 준비한 소스와 KR/JP 음성 이미지를 사용하며, 별도의 prepare나 configure는 필요하지 않다.
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
적용 제외 목록이 있으면 후보 태그·커밋과 원본 대체 파일을 확인하여 `upstream-adoptions.json`도 갱신한다.
업데이트는 운영 컨테이너를 교체하지 않는다.

로컬 에셋은 같은 Full Release의 public/assets/local와 data/local-assets.json만 함께 추출한다.
ZIP SHA256, 경로, manifest 항목 수와 모든 참조 파일을 확인한 뒤 버전별 폴더에 보관한다.
기존 운영 볼륨을 덮어쓰지 않고 configure에서 해당 버전 경로를 선택한다.
원본 0.1.3에서 0.2.1로는 소환물 39개·파일 117개가 추가되며, manifest는 1481에서 1598개가 된다.
0.2.1·0.2.2·0.2.3의 로컬 manifest 1598항목과 로컬 파일 1611개는 동일하지만, 버전별 Full Release에서
다시 추출하고 검증한다. 0.2.3도 해당 버전의 Full Release를 SHA256 검증 후 사용한다.
일본어 음성은 갱신한 원본의 다운로드 결과를 검증해 재사용한다.
한국어 음성은 별도의 KR 빌드를 사용하며, Full Release의 중국어 음성을 복사하지 않는다.

0.2.2의 개별 오퍼레이터 잠재능력·육성 설정, 통계 페이지, AI 팀원 마지막 선택과 게임 규칙 수정은
원본 동작을 사용한다. 재시작 투표 후에도 개별 육성 설정과 AI 선택 순서는 유지한다.
이름 화면의 통계 버튼은 설정·전체화면과 함께 우하단에 배치한다. 음성 설정은 원본의
`settings.voiceLang` 하나로 관리하며, 이전 `voiceLanguage` 선택값은 최초 이관에 사용한다.
한국어가 기본이며, KR/JP 팩에서 선택 언어의 음성이 없으면 다른 언어로 대체하지 않고 무음 처리한다.
0.2.3의 글자 크기 선택, 오퍼레이터별 음성, 홈 화면 설치와 접속 복구는 원본 기능을 보존한다.
설치·복구 버튼은 이름 화면 좌측 하단에 표시하고 통계·설정·전체화면은 우하단에 유지한다.
한국어 교정 164개 중 원본에 반영되거나 같은 의미로 정리된 162개와 삭제된 문구 1개는 적용에서 제외하고,
정예 오퍼레이터 판매 문구 교정 1개는 `ko-ui.json`에 남긴다. 추가 기능 문구는 계속 `ko-features.json`에서 관리한다.
좁은 모바일 화면의 로비·파티 상단은 제목과 버튼을 두 줄로 나누어 통계·설정·플레이 방법이
제목을 가리지 않도록 한다.

검증한 lock·패치·스크립트를 main에 합쳐 push한 다음 service/에서 docker compose -f stack.cf-tunnel.yaml up -d로 운영에 적용한다.
반영 후 보조 검사 python3 ../scripts/project.py verify를 사용할 수 있다.
원본 tag 이름은 lock에 기록하며, 이 자체 저장소에는 원본 tag를 가져올 필요가 없다.
자체 릴리스에는 ko/v0.2.3-r1 같은 이름을 사용할 수 있다.

## 상태와 종료

~~~bash
cd service
docker compose -f stack.cf-tunnel.yaml ps
docker compose -f stack.cf-tunnel.yaml down
docker compose -f stack.cf-tunnel.yaml up -d
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
