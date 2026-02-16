import os
import json
import re
from pathlib import Path
from typing import Optional, Dict, Any

def browser_login(court_code: str, appurl: Optional[str] = None, headed: bool = False, wait_ms: int = 0) -> dict:
    """Perform CSO login with a real browser via Playwright, then persist cookies.

    Returns a dict with status and details. Requires environment variables:
    PACER_USERNAME, PACER_PASSWORD.
    """
    username = os.getenv('PACER_USERNAME')
    password = os.getenv('PACER_PASSWORD')
    if not (username and password):
        return {"ok": False, "error": "PACER credentials not set"}

    try:
        from urllib.parse import quote
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return {"ok": False, "error": f"Playwright not available: {e}"}

    base = court_code.upper()
    if base.endswith('D'):
        base = base[:-1]
    court_id = f"{base}DC" if court_code != "cacd" else "CACDC"
    ret_url = appurl or f"https://ecf.{court_code}.uscourts.gov/cgi-bin/DktRpt.pl"
    login_url = f"https://pacer.login.uscourts.gov/csologin/login.jsf?pscCourtId={court_id}&appurl={quote(ret_url, safe='')}"

    cookies_out = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context()
        page = context.new_page()

        page.goto(login_url, wait_until="load")
        # Fill username/password
        # PACER uses Jakarta Faces; use robust selectors
        try:
            page.fill("input[id='loginForm:loginName']", username)
        except Exception:
            page.fill("input[name*='loginName']", username)
        try:
            page.fill("input[id='loginForm:password']", password)
        except Exception:
            page.fill("input[name*='loginForm'][type='password']", password)

        # Click login button
        try:
            page.click("button[name='loginForm:fbtnLogin']")
        except Exception:
            # Fallback to any submit button
            page.click("button[type='submit']")

        # Wait for navigation back to ECF host
        page.wait_for_load_state("networkidle")
        # Sanity: ensure we left the PACER login origin
        current_url = page.url
        if "pacer.login.uscourts.gov" in current_url:
            # Try to follow redirect if present
            try:
                page.wait_for_url(lambda u: "uscourts.gov" in u and f"ecf.{court_code}.uscourts.gov" in u, timeout=8000)
            except Exception:
                pass

        # If running headed, give user time to complete any interstitials (accept/continue)
        if headed and wait_ms and wait_ms > 0:
            try:
                page.wait_for_timeout(wait_ms)
            except Exception:
                pass

        # Collect cookies from context (all domains)
        cookies_out = context.cookies()
        browser.close()

    # Persist cookies in the format expected by PacerClient
    out_path = Path(__file__).resolve().parent.parent / ".pacer_cookies.json"
    to_store = []
    for c in cookies_out:
        to_store.append({
            'name': c.get('name'),
            'value': c.get('value'),
            'domain': c.get('domain'),
            'path': c.get('path', '/'),
            'secure': bool(c.get('secure', False)),
            'expires': c.get('expires')
        })
    out_path.write_text(json.dumps(to_store))

    return {"ok": True, "saved": len(to_store), "cookie_file": str(out_path), "current_url": current_url}

if __name__ == "__main__":
    import sys
    cc = sys.argv[1] if len(sys.argv) > 1 else os.getenv('PACER_COURT', 'flsd')
    ret = browser_login(cc)
    print(json.dumps(ret, indent=2))

def _derive_doc_id(url: str) -> str:
    try:
        m = re.search(r"/doc1/([^/?#]+)", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    # Fallback to hash-like name
    import hashlib
    return hashlib.sha256(url.encode()).hexdigest()[:32]

def browser_fetch_document(court_code: str, doc_url: str) -> Dict[str, Any]:
    """Use Playwright to fetch a doc1 PDF and save under docs/<court>/<docid>.pdf.
    Requires cookies (bootstrap first for best results).
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    cookies_path = Path(__file__).resolve().parent.parent / ".pacer_cookies.json"
    out_dir = Path(__file__).resolve().parent.parent / "docs" / court_code
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_id = _derive_doc_id(doc_url)
    out_path = out_dir / f"{doc_id}.pdf"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        # Load cookies if present
        if cookies_path.exists():
            try:
                data = json.loads(cookies_path.read_text())
                pw_cookies = []
                for c in data:
                    pw_cookies.append({
                        'name': c.get('name'),
                        'value': c.get('value'),
                        'domain': c.get('domain'),
                        'path': c.get('path', '/'),
                        'secure': bool(c.get('secure', False)),
                        'expires': c.get('expires') or 0,
                        'httpOnly': False,
                        'sameSite': 'Lax'
                    })
                context.add_cookies(pw_cookies)
            except Exception:
                pass
        page = context.new_page()
        # Try to download directly
        try:
            with page.expect_download(timeout=20000) as dl_wait:
                page.goto(doc_url, wait_until="load")
            download = dl_wait.value
            download.save_as(str(out_path))
            browser.close()
            return {"ok": True, "path": str(out_path), "filename": out_path.name, "doc_id": doc_id}
        except PWTimeout:
            # Attempt to bootstrap cookies specifically for this doc
            try:
                browser.close()
            except Exception:
                pass
            login_res = browser_login(court_code, appurl=doc_url)
            # Re-open browser and retry download
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            # Reload cookies
            if cookies_path.exists():
                try:
                    data = json.loads(cookies_path.read_text())
                    pw_cookies = []
                    for c in data:
                        pw_cookies.append({
                            'name': c.get('name'),
                            'value': c.get('value'),
                            'domain': c.get('domain'),
                            'path': c.get('path', '/'),
                            'secure': bool(c.get('secure', False)),
                            'expires': c.get('expires') or 0,
                            'httpOnly': False,
                            'sameSite': 'Lax'
                        })
                    context.add_cookies(pw_cookies)
                except Exception:
                    pass
            page = context.new_page()
            try:
                with page.expect_download(timeout=25000) as dl_wait:
                    page.goto(doc_url, wait_until="load")
                download = dl_wait.value
                download.save_as(str(out_path))
                browser.close()
                return {"ok": True, "path": str(out_path), "filename": out_path.name, "doc_id": doc_id}
            except Exception as e:
                try:
                    browser.close()
                except Exception:
                    pass
                return {"ok": False, "error": f"download timeout: {e}", "doc_id": doc_id}
        except Exception as e:
            try:
                browser.close()
            except Exception:
                pass
            return {"ok": False, "error": str(e), "doc_id": doc_id}

async def async_browser_fetch_document(court_code: str, doc_url: str) -> Dict[str, Any]:
    from playwright.async_api import async_playwright, TimeoutError as APWTimeout
    from urllib.parse import quote
    cookies_path = Path(__file__).resolve().parent.parent / ".pacer_cookies.json"
    out_dir = Path(__file__).resolve().parent.parent / "docs" / court_code
    out_dir.mkdir(parents=True, exist_ok=True)
    doc_id = _derive_doc_id(doc_url)
    out_path = out_dir / f"{doc_id}.pdf"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True)
        if cookies_path.exists():
            try:
                data = json.loads(cookies_path.read_text())
                pw_cookies = []
                for c in data:
                    pw_cookies.append({
                        'name': c.get('name'),
                        'value': c.get('value'),
                        'domain': c.get('domain'),
                        'path': c.get('path', '/'),
                        'secure': bool(c.get('secure', False)),
                        'expires': c.get('expires') or 0,
                        'httpOnly': False,
                        'sameSite': 'Lax'
                    })
                await context.add_cookies(pw_cookies)
            except Exception:
                pass
        page = await context.new_page()
        try:
            # Navigate and try anchor-based download clicks
            await page.goto(doc_url, wait_until="domcontentloaded")
            async def try_click_download(pg):
                sels = ["a[href*='/doc1/']", "a[href*='show_temp.pl']"]
                for s in sels:
                    loc = pg.locator(s)
                    if await loc.count() > 0:
                        try:
                            async with pg.expect_download(timeout=15000) as dlw:
                                await loc.first.click()
                            d = await dlw.value
                            await d.save_as(str(out_path))
                            return True
                        except Exception:
                            continue
                # Try in iframes
                for f in pg.frames:
                    if f == pg.main_frame:
                        continue
                    for s in sels:
                        locf = f.locator(s)
                        if await locf.count() > 0:
                            try:
                                async with pg.expect_download(timeout=15000) as dlw2:
                                    await locf.first.click()
                                d2 = await dlw2.value
                                await d2.save_as(str(out_path))
                                return True
                            except Exception:
                                continue
                return False

            if await try_click_download(page):
                await browser.close()
                return {"ok": True, "path": str(out_path), "filename": out_path.name, "doc_id": doc_id}
        except APWTimeout:
            base = court_code.upper()
            if base.endswith('D'):
                base = base[:-1]
            court_id = f"{base}DC" if court_code != "cacd" else "CACDC"
            login_url = f"https://pacer.login.uscourts.gov/csologin/login.jsf?pscCourtId={court_id}&appurl={quote(doc_url, safe='')}"
            await page.goto(login_url, wait_until="load")
            try:
                await page.fill("input[id='loginForm:loginName']", os.getenv('PACER_USERNAME',''))
            except Exception:
                await page.fill("input[name*='loginName']", os.getenv('PACER_USERNAME',''))
            try:
                await page.fill("input[id='loginForm:password']", os.getenv('PACER_PASSWORD',''))
            except Exception:
                await page.fill("input[name*='loginForm'][type='password']", os.getenv('PACER_PASSWORD',''))
            try:
                await page.click("button[name='loginForm:fbtnLogin']")
            except Exception:
                await page.click("button[type='submit']")
            await page.wait_for_load_state("networkidle")
            # After CSO login, attempt generic request-based retrieval
            from urllib.parse import urlparse, parse_qs, urlencode, urljoin
            from bs4 import BeautifulSoup
            parsed = urlparse(doc_url)
            qs = parse_qs(parsed.query or '')
            candidates = [doc_url]
            def add_param(u, k, v):
                pu = urlparse(u)
                q = parse_qs(pu.query)
                q[k] = [v]
                new_q = urlencode({kk: vv[0] if isinstance(vv, list) and vv else vv for kk, vv in q.items()})
                return pu._replace(query=new_q).geturl()
            candidates.append(add_param(doc_url, 'download', '1'))
            candidates.append(add_param(doc_url, 'pdf_header', '1'))
            if qs.get('caseid') and qs.get('de_seq_num'):
                host = f"{parsed.scheme}://{parsed.netloc}"
                show_temp = f"{host}/show_temp.pl?caseid={qs['caseid'][0]}&de_seq_num={qs['de_seq_num'][0]}&pdf_header=1"
                candidates.append(show_temp)

            async def try_via_requests(url: str, depth: int = 0) -> bool:
                if depth > 3:
                    return False
                resp = await context.request.get(url, headers={
                    'Accept': 'application/pdf,application/octet-stream;q=0.9,*/*;q=0.8',
                    'User-Agent': 'Mozilla/5.0 (compatible; CourtRSS/1.0)'
                })
                ct = (resp.headers.get('content-type') or '').lower()
                body = await resp.body()
                if 'pdf' in ct or (body[:5] == b'%PDF-'):
                    out_path.write_bytes(body)
                    return True
                text = await resp.text()
                soup = BeautifulSoup(text, 'html.parser')
                cand = None
                iframe = soup.find('iframe', src=lambda h: h and ('/doc1/' in h or 'show_temp.pl' in h))
                if iframe and iframe.get('src'):
                    cand = iframe.get('src')
                if not cand:
                    a = soup.find('a', href=lambda h: h and ('/doc1/' in h or 'show_temp.pl' in h))
                    if a and a.get('href'):
                        cand = a.get('href')
                if not cand:
                    form = soup.find('form', action=lambda h: h and ('/doc1/' in h or 'show_temp.pl' in h))
                    if form and form.get('action'):
                        action = form.get('action')
                        data = {}
                        for inp in form.find_all('input'):
                            name = inp.get('name')
                            value = inp.get('value') or ''
                            if name:
                                data[name] = value
                        target = action if action.startswith('http') else urljoin(url, action)
                        r2 = await context.request.post(target, form=data, headers={'User-Agent': 'Mozilla/5.0'})
                        ct2 = (r2.headers.get('content-type') or '').lower()
                        b2 = await r2.body()
                        if 'pdf' in ct2 or (b2[:5] == b'%PDF-'):
                            out_path.write_bytes(b2)
                            return True
                        return False
                if cand:
                    full = cand if cand.startswith('http') else urljoin(url, cand)
                    return await try_via_requests(full, depth+1)
                return False

            # First try existing page click approach
            await page.goto(doc_url, wait_until="domcontentloaded")
            if await try_click_download(page):
                await browser.close()
                return {"ok": True, "path": str(out_path), "filename": out_path.name, "doc_id": doc_id}
            # Otherwise, try request-based candidates
            for u in candidates:
                ok = await try_via_requests(u)
                if ok:
                    await browser.close()
                    return {"ok": True, "path": str(out_path), "filename": out_path.name, "doc_id": doc_id}
            await browser.close()
            return {"ok": False, "error": "Could not obtain PDF after request attempts", "doc_id": doc_id}
        except Exception as e:
            try:
                await browser.close()
            except Exception:
                pass
            return {"ok": False, "error": str(e), "doc_id": doc_id}
