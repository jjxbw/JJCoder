import json

from jjcoder.tools.project import ProjectDiagramTool, ProjectScanTool
from jjcoder.tools.web import WebFetchTool, WebSearchTool, _SearchParser, _validate_url


def test_project_scan_reports_python_files_and_imports(tmp_path):
    (tmp_path / "main.py").write_text("import json\nfrom jjcoder import Agent\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")

    result = json.loads(ProjectScanTool().execute(str(tmp_path)))

    assert result["file_count"] == 2
    assert result["entry_points"] == ["main.py"]
    main = next(item for item in result["modules"] if item["path"] == "main.py")
    assert "json" in main["imports"]


def test_project_diagram_supports_both_mermaid_styles(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")

    assert "mindmap" in ProjectDiagramTool().execute(str(tmp_path), "mindmap")
    assert "flowchart LR" in ProjectDiagramTool().execute(str(tmp_path), "architecture")


def test_search_parser_handles_lite_results():
    parser = _SearchParser()
    parser.feed(
        "<a class='result-link' href='https://example.com'>Example</a>"
        "<td class='result-snippet'>A useful result</td>"
    )

    assert parser.results == [{"title": "Example", "url": "https://example.com", "snippet": "A useful result"}]


def test_web_fetch_extracts_html(monkeypatch):
    monkeypatch.setattr(
        "jjcoder.tools.web._request",
        lambda url, timeout=15: (url, "text/html", b"<title>Demo</title><script>ignore()</script><p>Hello world</p>"),
    )

    result = json.loads(WebFetchTool().execute("https://example.com"))

    assert result["title"] == "Demo"
    assert result["content"] == "Demo\nHello world"


def test_web_search_empty_query_and_ssrf_are_rejected():
    assert "must not be empty" in WebSearchTool().execute(" ")
    assert "private" in (_validate_url("http://127.0.0.1") or "")
