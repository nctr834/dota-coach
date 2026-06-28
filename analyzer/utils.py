from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup


# OpenDota API provides easier access to build data it seems.. using this for performance testing
def get_dota2protracker_page(hero, include_meta=False, include_off_meta=False):
    url = f"https://dota2protracker.com/hero/{hero}"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_selector("[data-track-view='hero-builds']", timeout=15000)
        page.wait_for_timeout(2000)

        def clean_html(html, track_view):
            soup = BeautifulSoup(html, "html.parser")

            for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            # Replace imgs with their alt text
            for img in soup.find_all("img"):
                alt_text = img.get("title") or img.get("alt") or ""
                img.replace_with(f"[{alt_text}]")
            section = soup.find(attrs={"data-track-view": track_view})

            return section.get_text(separator="\n", strip=True) if section else None

        def get_content_after_click(track_selector, track_view):
            btn = page.query_selector(f"[data-track='{track_selector}']")
            if btn:
                btn.click()
                page.wait_for_timeout(1500)
                return clean_html(page.content(), track_view)
            return None

        builds = clean_html(page.content(), "hero-builds") or ""
        content = {"hero-builds": builds.split("Neutral Items")[0]}
        if include_meta:
            meta = get_content_after_click("hero-tab-meta", "hero-meta-analysis") or ""
            content["hero-meta"] = meta.split("against\nCarry\nMeta")[0]
        if include_off_meta:
            off_meta = (
                get_content_after_click("hero-tab-offmeta", "hero-off-meta") or ""
            )
            content["hero-off-meta"] = "".join(off_meta.split("Off-Meta Score")[0:2])
        return content
