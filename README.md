# AN Stronghold Protocol 한국어 패치

[Stronghold-Protocol](https://github.com/sganggs/Stronghold-Protocol)에 적용하는 한국어 교정과
추가 수정, 빌드·운영 스크립트를 관리하는 독립 저장소입니다.
원본 소스는 이 저장소의 현재 파일과 새 Git 이력에 포함하지 않습니다.

원본은 [upstream.lock.json](upstream.lock.json)에 기록한 **v0.2.1 / 전체 커밋 SHA**로 받습니다.
순수 원본은 Git 제외 캐시에 보관하고, 한국어 패치를 적용한 별도 소스로 이미지를 빌드합니다.
이 설치의 분리 전 Git 이력은 로컬 archive 폴더의 검증된 bundle로 보관합니다.

| 저장소 파일 | 역할 |
| --- | --- |
| [patches/ko-ui.json](patches/ko-ui.json) | 한국어 문구 교정 164개와 기존 값 가드 |
| patches/*.patch | Docker 한국어 음성 빌드와 기타 소스 수정 |
| overlays/ | 원본에 추가할 자체 파일: 음성 검사기와 테스트 |
| deploy/ | 운영·개발 설정 템플릿 및 검사 이미지 |
| [scripts/project.py](scripts/project.py) | 원본 준비, 업데이트, 패치 추출과 구성 CLI |
| [scripts/deploy.py](scripts/deploy.py) | 에셋 추출, 이미지 빌드, 서비스·복구 관리 |
| tests/ | 자체 구성 스크립트의 테스트 |

원본 게임 기능·전투 로직·밸런스는 유지합니다. 일부 UI 기계번역은 아직 남아 있습니다.
한국어 음성은 공개 KR 덤프를 사용하며, 해당 음성이 없는 오퍼레이터는 무음입니다.

WSL Ubuntu/Linux, Python 3.10 이상, Git, Docker 및 Docker Compose가 필요합니다.
호스트 Node.js 설치는 필요하지 않습니다.

~~~bash
# 지정한 원본 준비 → 한국어 패치 적용
python3 scripts/project.py prepare

# 로컬 에셋 검증/추출 → 서비스 설정 생성 → KR 이미지 빌드
python3 scripts/project.py setup

# 구성·음성·게임 검증 후 서비스 시작
python3 scripts/project.py up
python3 scripts/project.py verify
~~~

처음 설치하면 service/.env에 Tunnel token을 직접 입력합니다.
기존 설치의 비밀값은 스크립트가 보존합니다. 실제 운영 폴더, 원본 캐시와 생성 소스는 Git에서 제외합니다.

~~~bash
python3 scripts/project.py status
python3 scripts/project.py down
python3 scripts/project.py rollback
~~~

추가 한글화, 기타 수정 및 원본 업데이트 방법은 [운영 안내](docs/DEPLOY_KO.md)에 있습니다.
코드와 파생 패치는 [GPL-3.0-or-later](LICENSE), 원본과 게임 에셋의 고지는 [NOTICE.md](NOTICE.md)를 따릅니다.
