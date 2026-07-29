from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Page, async_playwright


DEFAULT_COURSE_URL = (
    "https://appaoswidcd4711.h5.xiaoeknow.com/p/course/column/"
    "p_69e590e6e4b0694c5bb2e455?product_id=p_69e590e6e4b0694c5bb2e455"
)
DEFAULT_PROFILE = "C:/Users/Administrator/.xiaoe-hls-poc/profiles/stock_agent_batch"
DEFAULT_CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe"
DEFAULT_OUT = "storage/runtime/xiaoe_batch_cache"


@dataclass
class CourseItem:
    resource_id: str
    title: str
    start_at: str
    jump_url: str
    resource_type: int | None = None
    view_count: int | None = None
    learn_progress: int | None = None


@dataclass
class PlayInfo:
    resource_id: str
    title: str
    start_at: str
    page_url: str
    material_id: str | None
    mp3_url: str | None
    hls_url: str | None
    duration_seconds: int | None
    cover_url: str | None
    author_name: str | None
    captured_at: float


def _load_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _walk_items(payload: Any) -> list[CourseItem]:
    items: list[CourseItem] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("resource_id") and value.get("resource_title") and value.get("jump_url"):
                items.append(
                    CourseItem(
                        resource_id=str(value.get("resource_id") or ""),
                        title=str(value.get("resource_title") or ""),
                        start_at=str(value.get("start_at") or ""),
                        jump_url=str(value.get("jump_url") or ""),
                        resource_type=value.get("resource_type"),
                        view_count=value.get("view_count"),
                        learn_progress=value.get("learn_progress"),
                    )
                )
            for nested in value.values():
                walk(nested)
        elif isinstance(value, list):
            for nested in value:
                walk(nested)

    walk(payload)
    seen: set[str] = set()
    unique: list[CourseItem] = []
    for item in items:
        if item.resource_id in seen:
            continue
        seen.add(item.resource_id)
        unique.append(item)
    return unique


def _extract_play_info(item: CourseItem, page_url: str, payloads: list[dict[str, Any]]) -> PlayInfo | None:
    material_id = None
    mp3_url = None
    hls_url = None
    duration_seconds = None
    cover_url = None
    author_name = None
    play_payload = None
    availability_payload = None

    for payload in payloads:
        if not isinstance(payload, dict) or payload.get("code") != 0:
            continue
        data = payload.get("data")
        if isinstance(data, dict):
            if any(isinstance(v, dict) and "play_list" in v for v in data.values()):
                play_payload = payload
            if isinstance(data.get("video_info"), dict):
                availability_payload = payload

    if isinstance(availability_payload, dict):
        video_info = ((availability_payload.get("data") or {}).get("video_info") or {})
        duration_seconds = video_info.get("video_length") or duration_seconds
        cover_url = video_info.get("cover_img_url") or video_info.get("patch_img_url")
        author_name = video_info.get("author")
        material_id = video_info.get("material_id") or material_id
        mp3_url = video_info.get("video_audio_url") or mp3_url

    if isinstance(play_payload, dict):
        data = play_payload.get("data") or {}
        entry = next((v for v in data.values() if isinstance(v, dict) and "play_list" in v), None)
        if isinstance(entry, dict):
            material_id = entry.get("material_id") or material_id
            play_list = entry.get("play_list") or {}
            mp3 = play_list.get("mp3") or {}
            hls = play_list.get("720p_hls") or play_list.get("hls") or {}
            mp3_url = mp3.get("play_url") or mp3_url
            hls_url = hls.get("play_url") or hls_url

    if not mp3_url and not hls_url:
        return None
    return PlayInfo(
        resource_id=item.resource_id,
        title=item.title,
        start_at=item.start_at,
        page_url=page_url,
        material_id=material_id,
        mp3_url=mp3_url,
        hls_url=hls_url,
        duration_seconds=int(duration_seconds) if duration_seconds else None,
        cover_url=cover_url,
        author_name=author_name,
        captured_at=time.time(),
    )


def _redact_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.split("?", 1)[0] + "?<redacted>" if "?" in url else url


async def _wait_for_login(page: Page, profile_dir: Path) -> None:
    sentinel = profile_dir / ".xiaoe-batch-continue"
    sentinel.unlink(missing_ok=True)
    print("需要一次登录/授权确认。请在打开的 Chrome 窗口完成登录并确认能看到课程，然后创建继续文件或回到这里按 Ctrl+C 取消。")
    print(f"继续文件: {sentinel}")
    while not sentinel.exists():
        await page.wait_for_timeout(1000)
    sentinel.unlink(missing_ok=True)


async def _capture(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(args.profile)
    chrome = args.chrome or os.getenv("XIAOE_BROWSER_EXECUTABLE") or DEFAULT_CHROME
    captured_responses: list[dict[str, Any]] = []
    print(f"启动批量抓取 profile={profile_dir}", flush=True)

    async with async_playwright() as p:
        browser = None
        if args.cdp_url:
            browser = await p.chromium.connect_over_cdp(args.cdp_url)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            await page.set_viewport_size({"width": 430, "height": 900})
        else:
            context = await p.chromium.launch_persistent_context(
                str(profile_dir),
                executable_path=chrome,
                headless=False,
                viewport={"width": 430, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else await context.new_page()

        async def on_response(response) -> None:
            url = response.url
            if not any(key in url for key in ("column.items.get", "getPlayUrl", "resource.available.get")):
                return
            item = {
                "url": url,
                "status": response.status,
                "method": response.request.method,
            }
            try:
                item["body"] = await response.text()
            except Exception as exc:  # noqa: BLE001
                item["body_error"] = repr(exc)
            captured_responses.append(item)

        page.on("response", lambda response: asyncio.create_task(on_response(response)))
        await page.goto(args.course_url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(5000)
        if "login/auth" in page.url:
            await _wait_for_login(page, profile_dir)
            await page.goto(args.course_url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(5000)

        def collect_course_items() -> list[CourseItem]:
            list_payloads = [_load_json(r.get("body")) for r in captured_responses if "column.items.get" in r.get("url", "")]
            collected: list[CourseItem] = []
            for payload in list_payloads:
                collected.extend(_walk_items(payload))
            return collected

        course_items = collect_course_items()
        if not course_items:
            print(f"当前页面未返回课程列表: {page.url}")
            await _wait_for_login(page, profile_dir)
            captured_responses.clear()
            await page.goto(args.course_url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(5000)
            course_items = collect_course_items()
        seen: set[str] = set()
        course_items = [item for item in course_items if not (item.resource_id in seen or seen.add(item.resource_id))]
        course_items = course_items[: args.limit]
        if not course_items:
            raise RuntimeError("未捕获课程列表，请确认已登录并能访问课程目录。")

        play_infos: list[PlayInfo] = []
        base = "https://appaoswidcd4711.h5.xiaoeknow.com"
        for index, item in enumerate(course_items, start=1):
            before_count = len(captured_responses)
            target = item.jump_url if item.jump_url.startswith("http") else base + item.jump_url
            print(f"[{index}/{len(course_items)}] {item.start_at} {item.title}")
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(args.wait_ms)
                try:
                    await page.evaluate(
                        """() => {
                            const video = document.querySelector('video');
                            if (video) {
                                video.muted = true;
                                return video.play().catch(() => null);
                            }
                            return null;
                        }"""
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(args.wait_ms)
            except Exception as exc:  # noqa: BLE001
                print(f"  跳过: {exc}")
                continue
            payloads = [
                _load_json(r.get("body"))
                for r in captured_responses[before_count:]
                if any(key in r.get("url", "") for key in ("getPlayUrl", "resource.available.get"))
            ]
            info = _extract_play_info(item, page.url, [p for p in payloads if p])
            if info is None:
                print("  未拿到播放地址")
                continue
            print(f"  OK material={info.material_id or '-'} duration={info.duration_seconds or '-'}")
            play_infos.append(info)

        await context.storage_state(path=str(out_dir / "storage-state.json"))
        if browser is not None:
            await browser.close()
        else:
            await context.close()

    private_records = [asdict(info) for info in play_infos]
    public_records = []
    for info in play_infos:
        row = asdict(info)
        row["mp3_url"] = _redact_url(row.get("mp3_url"))
        row["hls_url"] = _redact_url(row.get("hls_url"))
        public_records.append(row)

    (out_dir / "play_urls.private.json").write_text(json.dumps(private_records, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "play_urls.public.json").write_text(json.dumps(public_records, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "raw_responses.json").write_text(json.dumps(captured_responses, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已缓存 {len(play_infos)} 条播放信息: {out_dir}")
    return 0 if play_infos else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量缓存小鹅通课程最近多节视频播放信息。")
    parser.add_argument("--course-url", default=DEFAULT_COURSE_URL)
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--chrome", default=DEFAULT_CHROME)
    parser.add_argument("--out-dir", default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--wait-ms", type=int, default=4000)
    parser.add_argument("--cdp-url", help="连接已打开的可见 Chrome，例如 http://127.0.0.1:9223")
    return parser.parse_args()


def main() -> int:
    return asyncio.run(_capture(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
