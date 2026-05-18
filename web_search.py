from ddgs import DDGS
import trafilatura


def fetch_page_text(url: str) -> str:
    downloaded = trafilatura.fetch_url(url)
    return trafilatura.extract(downloaded) or "Failed to extract"


with DDGS() as ddgs:
    results = list(ddgs.text("transformer architecture", max_results=5))
    print(results)
    print("Full Body")
    print(fetch_page_text(results[0]["href"]))
