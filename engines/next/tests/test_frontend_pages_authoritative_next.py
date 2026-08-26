from pathlib import Path

WORKFLOW = Path('.github/workflows/frontend_pages.yml')
STABLE_FALLBACK = Path('.github/workflows/deploy_latest_stable_preview.yml')


def test_frontend_pages_uses_authoritative_next_snapshot():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'Garrincha077/stock-screener2' not in text
    assert 'data/daily_scans/latest_scan_meta.json' in text
    assert 'frontend/public/data/latest.json' in text
    assert '--source-repository "${{ github.repository }}"' in text
    assert '--source-ref main' in text
    assert 'Deploy authoritative Next terminal build to GitHub Pages' in text


def test_stable_fallback_remains_isolated():
    text = STABLE_FALLBACK.read_text(encoding='utf-8')
    assert 'Garrincha077/stock-screener2' in text
    assert 'Stable fallback' in text or 'Stable snapshot' in text
