# -*- coding: utf-8 -*-
"""모바일/작성기.html 로 웹 배포용 폴더(모바일/웹배포)를 만듭니다.

같은 작성기.html 로 주소 두 개를 만듭니다. (코드는 한 파일, 앱만 둘)
  모바일/웹배포/        → 캐릭터 작성기   https://m1nch0wh4ck.github.io/charameka/
  모바일/웹배포/html/   → HTML 미리보기   https://m1nch0wh4ck.github.io/charameka/html/
각각 따로 앱으로 설치되고 아이콘·이름이 다릅니다. 저장 내용은 같은 주소(origin)를 쓰므로 양쪽에서 이어집니다.

만들어지는 파일: index.html, manifest.webmanifest, sw.js, .nojekyll, README.md + html/ 아래 3개
(아이콘 PNG 는 이 폴더에 미리 들어 있습니다.)
만든 뒤 main 에 올리면 GitHub Actions(.github/workflows/pages.yml)가 모바일/웹배포 를 자동으로 배포합니다.
(작성 내용 저장 키 'melloy-writer:*' 는 기존 기기 데이터를 잇기 위해 그대로 둡니다.)
실행: python 모바일/웹배포_만들기.py   (프로그램 폴더에서)
"""
import json
from pathlib import Path

SRC = Path("모바일/작성기.html")
OUT = Path("모바일/웹배포")
VERSION = "8"  # 내용이 바뀌면 올려서 휴대폰의 옛 캐시를 비웁니다

# 앱마다 다른 것 (아이콘은 작성기가 루트, 미리보기가 html/ 에서 '../' 로 가져다 씁니다)
APPS = {
    "writer": {
        "dir": "",
        "app": "writer",
        "title": "MELLOY 캐릭터 작성기",
        "short": "MELLOY 작성기",
        "apple": "MELLOY 작성기",
        "desc": "템플릿 칸에 자연어로 채우면 MELLOY 양식의 변환 파일을 만들어 주는 작성 도구",
        "theme": "#0d6b63",
        "id": "/charameka/",
        "icon": "",
        "cache": "charameka-writer",
    },
    "html": {
        "dir": "html",
        "app": "html",
        "title": "상태창·소개문 HTML 미리보기",
        "short": "HTML 미리보기",
        "apple": "HTML 미리보기",
        "desc": "멜로이·루모의 상태창·소개문 HTML을 붙여넣어 폭·배경·변수를 바꿔 보고 막히는 태그를 확인하는 도구",
        "theme": "#16456b",
        "id": "/charameka/html/",
        "icon": "-html",
        "cache": "charameka-html",
    },
}


def head(a):
    """앱마다 제목·색·아이콘·manifest 만 다른 머리말. 본문(작성기.html)은 두 앱이 똑같습니다."""
    up = "../" if a["dir"] else ""
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="{a["theme"]}">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{a["apple"]}">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" type="image/png" sizes="192x192" href="{up}icon{a["icon"]}-192.png">
<link rel="apple-touch-icon" href="{up}apple-touch-icon{a["icon"]}.png">
<style>:root{{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}}body{{margin:0}}img{{max-width:100%}}[hidden]{{display:none!important}}</style>
<script>window.CHARAMEKA_APP = "{a["app"]}";</script>
<script>if ("serviceWorker" in navigator && window.isSecureContext) addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => {{}}));</script>
</head>
<body>
"""


def manifest(a):
    up = "../" if a["dir"] else ""
    return {
        "id": a["id"],
        "name": a["title"],
        "short_name": a["short"],
        "description": a["desc"],
        "lang": "ko",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#f2f5f4",
        "theme_color": a["theme"],
        "icons": [
            {"src": f"{up}icon{a['icon']}-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": f"{up}icon{a['icon']}-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": f"{up}icon{a['icon']}-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }


# 인터넷이 될 때는 항상 최신 파일을 받고, 안 될 때만 저장해 둔 파일로 엽니다.
# 앱마다 자기 폴더의 sw 를 등록하므로(상대경로), 캐시 이름과 파일 목록도 앱마다 따로입니다.
SW = """const CACHE = "__CACHE__-v__VERSION__";
const FILES = __FILES__;
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES))); self.skipWaiting(); });
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;
  e.respondWith(fetch(req).then((res) => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)); }
    return res;
  }).catch(() => caches.match(req, { ignoreSearch: true }).then((hit) => hit || caches.match("./"))));
});
"""


def sw(a):
    up = "../" if a["dir"] else ""
    files = ["./", "./index.html", "./manifest.webmanifest"]
    files += [f"{up}icon{a['icon']}-192.png", f"{up}icon{a['icon']}-512.png",
              f"{up}icon{a['icon']}-maskable-512.png", f"{up}apple-touch-icon{a['icon']}.png"]
    # 캐시 지우기는 자기 캐시 이름만 건드리므로, 두 앱이 서로의 저장분을 지우지 않습니다.
    return (SW.replace("__CACHE__", a["cache"]).replace("__VERSION__", VERSION)
            .replace("__FILES__", json.dumps(files)).replace('const CACHE', 'const CACHE', 1)
            .replace("keys.filter((k) => k !== CACHE)", f'keys.filter((k) => k !== CACHE && k.startsWith("{a["cache"]}-"))'))


README = """# MELLOY 캐릭터 작성기 (모바일)

휴대폰 브라우저에서 쓰는 캐릭터 설정 작성 도구입니다. 개인이 만든 도구이며 플랫폼 공식 도구가 아닙니다.

주소가 둘로 나뉘어 있고, 각각 따로 앱으로 설치됩니다.

| 주소 | 앱 | 하는 일 |
|---|---|---|
| `/charameka/` | MELLOY 캐릭터 작성기 | 템플릿 칸 작성·실시간 점검·내보내기 |
| `/charameka/html/` | HTML 미리보기 | 상태창·소개문 HTML 확인 |

서로 메뉴에서 한 번에 넘어갈 수 있고, 저장 내용은 각자 따로 보관됩니다.

- 템플릿 칸에 자연어로 채우면 템플릿 양식의 변환 파일(.md)을 만들어 줍니다.
- 1인 / 다인(다인챗) 모드를 고를 수 있습니다. 다인 모드는 인물별 카드를 여러 장 쓰고, 모드마다 작업이 따로 저장됩니다.
- 등장인물·설명·합계 글자 수, 변수 200자, `{{user}}` 위치 등을 쓰는 동안 바로 점검합니다.
- **HTML 미리보기**: 멜로이·루모의 상태창·소개문 HTML을 붙여넣어 폭(380 / 768 / 전체)·배경(밝게 / 어둡게)을 바꿔 보고, `{{user}}` 같은 변수에 임시 값을 넣어 확인합니다. 플랫폼 규정상 막히는 태그도 찾아 줍니다.
- 작성 내용은 각자 기기의 브라우저에만 저장되고, 어디에도 전송되지 않습니다.
- 안드로이드 크롬: 메뉴(⋮) → **앱 설치** / 아이폰 사파리: 공유 → **홈 화면에 추가** 로 앱처럼 쓸 수 있습니다.
- 한 번 열어 두면 인터넷이 없어도 열립니다.

글꼴: Google Fonts (Gowun Batang, IBM Plex Sans KR, IBM Plex Mono — SIL Open Font License)
"""


def main():
    page = SRC.read_text(encoding="utf-8")
    for a in APPS.values():
        d = OUT / a["dir"] if a["dir"] else OUT
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.html").write_text(head(a) + page + "\n</body>\n</html>\n", encoding="utf-8")
        (d / "manifest.webmanifest").write_text(json.dumps(manifest(a), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (d / "sw.js").write_text(sw(a), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    (OUT / "README.md").write_text(README, encoding="utf-8")
    print(f"{OUT} 에 웹 배포 파일을 만들었습니다. (작성기: ./  ·  HTML 미리보기: ./html/)")


if __name__ == "__main__":
    main()
