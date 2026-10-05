import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


async def run(config_path: str) -> int:
    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "Playwright не установлен для выбранного Python. "
            "Задайте HH_CAPTCHA_PYTHON на Python из hh-applicant-tool (pipx).",
            file=sys.stderr,
        )
        return 2

    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    image_path = None
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context_options = {}
            if config.get("user_agent"):
                context_options["user_agent"] = config["user_agent"]
            context = await browser.new_context(**context_options)
            await context.add_cookies(config["cookies"])
            page = await context.new_page()

            try:
                await page.goto(
                    config["captcha_url"],
                    timeout=30000,
                    wait_until="domcontentloaded",
                )
            except PlaywrightTimeoutError:
                # HH оставляет фоновые запросы; проверим нужные элементы отдельно.
                pass

            image = page.locator('img[data-qa="account-captcha-picture"]')
            await image.wait_for(state="visible", timeout=15000)
            await page.wait_for_function(
                """() => {
                    const image = document.querySelector('img[data-qa="account-captcha-picture"]');
                    return image && image.complete && image.naturalWidth > 0;
                }""",
                timeout=15000,
            )
            image_bytes = await image.screenshot()

            fd, image_path = tempfile.mkstemp(prefix="hh-ai-captcha-", suffix=".png")
            os.close(fd)
            Path(image_path).write_bytes(image_bytes)
            os.chmod(image_path, 0o600)

            kitty = shutil.which("kitten")
            in_kitty = bool(
                os.environ.get("KITTY_WINDOW_ID") or os.environ.get("TERM") == "xterm-kitty"
            )
            if kitty and in_kitty:
                subprocess.run(
                    [kitty, "icat", "--stdin", "yes"],
                    input=image_bytes,
                    check=False,
                    stdout=sys.stderr,
                )
            else:
                print(f"Открой CAPTCHA-картинку: {image_path}", file=sys.stderr)

            try:
                sys.stderr.write("Текст с картинки HH: ")
                sys.stderr.flush()
                answer = input().strip()
            except EOFError:
                print("Нет интерактивного ввода в терминале.", file=sys.stderr)
                return 3
            if not answer:
                print("Пустой ответ CAPTCHA; запрос не повторяю.", file=sys.stderr)
                return 3

            field = page.locator('input[data-qa="account-captcha-input"]')
            await field.fill(answer)
            await field.press("Enter")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(1000)

            error = page.locator('[data-qa="account-captcha-error"]')
            if await error.is_visible():
                text = (await error.inner_text()).strip()
                print(f"HH не принял CAPTCHA: {text}", file=sys.stderr)
                return 4

            cookies = await context.cookies()
            print(json.dumps({"ok": True, "cookies": cookies}, ensure_ascii=False))
            return 0
        finally:
            await browser.close()
            if image_path:
                try:
                    os.unlink(image_path)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(sys.argv[1])))
