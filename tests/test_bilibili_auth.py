from __future__ import annotations

from http.cookiejar import Cookie, CookieJar

from engines.content.bilibili_auth import collect_bilibili_cookie_records, collect_cookie_names, create_cookie_file_from_header


def test_create_cookie_file_from_header(tmp_path):
    output = tmp_path / "bilibili.cookies.txt"
    result = create_cookie_file_from_header(
        "SESSDATA=test_sess; bili_jct=test_jct; DedeUserID=12345",
        output,
    )
    assert result == output.resolve()
    content = output.read_text(encoding="utf-8")
    assert "SESSDATA" in content
    assert ".bilibili.com" in content
    assert ".bilibili.cn" in content


def test_collect_bilibili_cookie_records_filters_domains():
    jar = CookieJar()
    jar.set_cookie(
        Cookie(
            version=0,
            name="SESSDATA",
            value="sess",
            port=None,
            port_specified=False,
            domain=".bilibili.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=True,
            expires=1893456000,
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None},
            rfc2109=False,
        )
    )
    jar.set_cookie(
        Cookie(
            version=0,
            name="sid",
            value="ignore-me",
            port=None,
            port_specified=False,
            domain=".example.com",
            domain_specified=True,
            domain_initial_dot=True,
            path="/",
            path_specified=True,
            secure=False,
            expires=None,
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False,
        )
    )

    records = collect_bilibili_cookie_records(jar)
    assert len(records) == 1
    assert records[0].name == "SESSDATA"
    assert records[0].domain == ".bilibili.com"


def test_collect_cookie_names_handles_duplicate_cookie_names_across_domains():
    jar = CookieJar()
    for domain in (".bilibili.com", ".bilibili.cn"):
        jar.set_cookie(
            Cookie(
                version=0,
                name="SESSDATA",
                value=f"sess-{domain}",
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=True,
                path="/",
                path_specified=True,
                secure=True,
                expires=1893456000,
                discard=False,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": None},
                rfc2109=False,
            )
        )

    cookie_names = collect_cookie_names(jar)
    assert cookie_names == {"SESSDATA"}
