# AN Stronghold Protocol 한국어 패치

패치 저장소: [D-Roote/AN_Stronghold-Protocol_Patch](https://github.com/D-Roote/AN_Stronghold-Protocol_Patch)

[Stronghold-Protocol](https://github.com/sganggs/Stronghold-Protocol)에 적용하는 한국어 교정과
추가 수정, 빌드·운영 스크립트를 관리하는 독립 저장소입니다.
원본 소스는 이 저장소의 현재 파일과 새 Git 이력에 포함하지 않습니다.

원본은 [upstream.lock.json](upstream.lock.json)에 기록한 **v0.2.1 / 전체 커밋 SHA**로 받습니다.
순수 원본은 Git 제외 캐시에 보관하고, 한국어 패치를 적용한 별도 소스로 이미지를 빌드합니다.
이 설치의 분리 전 Git 이력은 로컬 archive 폴더의 검증된 bundle로 보관합니다.

| 저장소 파일 | 역할 |
| --- | --- |
| [patches/ko-ui.json](patches/ko-ui.json) | 원본 UI의 한국어 문구 교정 164개와 충돌 검사 |
| [patches/ko-features.json](patches/ko-features.json) | 자체 추가 기능의 한국어 문구 41개와 충돌 검사 |
| patches/0001-Build-*.patch 등 | 숫자 순서와 Build-/UI-/Feat- 분류를 가진 소스 수정 |
| overlays/ | 원본에 추가할 자체 기능, 음성 검사기와 테스트 |
| deploy/ | 운영·개발 설정 템플릿 및 검사 이미지 |
| [scripts/project.py](scripts/project.py) | 원본 준비, 업데이트, 패치 추출과 구성 CLI |
| [scripts/deploy.py](scripts/deploy.py) | 에셋 추출, 최종 이미지 빌드와 Compose 구성 |
| tests/ | 자체 구성 스크립트의 테스트 |

전투 로직·밸런스는 유지하며 기본 한국어, 에셋 사전 다운로드, 팀 텍스트 채팅과 접이식 메뉴를 추가합니다.
일부 UI 기계번역은 아직 남아 있습니다.
한국어 음성은 공개 KR 덤프를 사용하며, 해당 음성이 없는 오퍼레이터는 무음입니다.

WSL Ubuntu/Linux, Python 3.10 이상, Git, Docker 및 Docker Compose가 필요합니다.
호스트 Node.js 설치는 필요하지 않습니다.

~~~bash
# 원본 받기·한국어 패치·로컬 에셋·최종 KR 이미지·Compose 설정까지 자동 준비
python3 scripts/project.py setup

# 이후 서비스는 Docker Compose로 관리
cd service
docker compose up -d
docker compose ps
~~~

별도로 prepare를 먼저 실행할 필요가 없습니다. 처음 설치하면 시작 전에 service/.env에
Tunnel token을 입력합니다. 기존 비밀값은 보존하며 .env 권한은 600으로 유지합니다.
실제 운영 폴더, 원본 캐시와 생성 소스는 Git에서 제외합니다.

~~~bash
# service/ 안에서 실행
docker compose logs -f
docker compose restart
docker compose down
~~~

패치나 원본 버전을 변경하면 프로젝트 루트에서 setup을 다시 실행합니다. 변경한 패치를 자동으로
재적용하고 새 이미지를 빌드하므로, 이후 service/에서 docker compose up -d로 반영합니다.
docker compose build도 준비된 소스로 KR 이미지를 다시 빌드할 수 있습니다.
보조 검사는 python3 scripts/project.py check / verify로 실행할 수 있습니다.

기본 배포는 복구본을 자동 생성하지 않습니다. 필요하면 서버를 중단하거나 재생성하여 적용합니다.

추가 한글화, 기타 수정 및 원본 업데이트 방법은 [운영 안내](docs/DEPLOY_KO.md)에 있습니다.
코드와 파생 패치는 [GPL-3.0-or-later](LICENSE), 원본과 게임 에셋의 고지는 [NOTICE.md](NOTICE.md)를 따릅니다.
