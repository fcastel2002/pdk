from __future__ import annotations

import asyncio

import pytest

from pdk import mcp_server, pages
from pdk.cli import build_parser, main
from pdk.sync import Outcome
from tests.helpers import label, view
from tests.test_sync import FakeGateway


@pytest.fixture
def gw() -> FakeGateway:
    fake = FakeGateway()
    fake.save("PDK/Detail", view(label("Detail")), actor="pdk")
    fake.save("PDK/Menu", view(label("Menu")), actor="pdk")
    fake.save("Home", view(label("Home")))  # a view of the project, outside PDK/
    fake.pages = {
        "/": {"viewPath": "Home"},
        "/about": {"viewPath": "About", "title": "About us"},
        "/menu": {"viewPath": "PDK/Menu"},
        "/pdk/PDK/Menu": {"viewPath": "PDK/Menu"},
        "/pdk/PDK/Detail": {"viewPath": "PDK/Detail"},
    }
    return fake


def test_list_sorts_the_routes_by_url_and_summarizes_the_preview_pages(gw: FakeGateway) -> None:
    assert pages.list(gw) == Outcome(True, "/ -> Home\n/about -> About\n/menu -> PDK/Menu\n"
                                           "+ 2 preview pages under /pdk/")


def test_list_says_when_there_are_no_routes(gw: FakeGateway) -> None:
    gw.pages = {"/pdk/PDK/Menu": {"viewPath": "PDK/Menu"}}
    assert pages.list(gw) == Outcome(True, "no routes\n+ 1 preview page under /pdk/")
    gw.pages = {}
    assert pages.list(gw) == Outcome(True, "no routes")


@pytest.mark.parametrize("url", ["/pdk/x", "/pdk"])
def test_add_rejects_urls_reserved_for_the_preview_pages(gw: FakeGateway, url: str) -> None:
    before = dict(gw.pages)
    outcome = pages.add(gw, url, "PDK/Detail")
    assert not outcome.ok and "reserved for the preview pages" in outcome.text
    assert gw.pages == before and gw.page_writes == 0


def test_add_rejects_a_url_without_a_leading_slash(gw: FakeGateway) -> None:
    outcome = pages.add(gw, "detail", "PDK/Detail")
    assert not outcome.ok and "starts with '/'" in outcome.text
    assert gw.page_writes == 0


def test_add_refuses_a_view_missing_from_the_sandbox_or_a_folder(gw: FakeGateway) -> None:
    assert pages.add(gw, "/new", "PDK/Missing") == Outcome(False, "PDK/Missing is not in the sandbox: deploy it first")
    assert not pages.add(gw, "/new", "PDK").ok  # PDK is a folder
    assert "/new" not in gw.pages and gw.page_writes == 0


def test_add_routes_the_url_to_the_view_with_its_title_when_given(gw: FakeGateway) -> None:
    assert pages.add(gw, "/detail", "PDK/Detail", "Detail") == Outcome(True, "added /detail -> PDK/Detail")
    assert gw.pages["/detail"] == {"viewPath": "PDK/Detail", "title": "Detail"}
    assert pages.add(gw, "/plain", "PDK/Menu").ok
    assert gw.pages["/plain"] == {"viewPath": "PDK/Menu"}


def test_add_is_idempotent_for_the_same_view(gw: FakeGateway) -> None:
    pages.add(gw, "/detail", "PDK/Detail")
    writes = gw.page_writes
    assert pages.add(gw, "/detail", "PDK/Detail") == Outcome(True, "/detail already opens PDK/Detail")
    assert gw.page_writes == writes


def test_add_refuses_a_url_that_opens_another_view_and_writes_nothing(gw: FakeGateway) -> None:
    before, writes = dict(gw.pages), gw.page_writes
    assert pages.add(gw, "/about", "PDK/Detail") == Outcome(False, "/about already opens About; pick another URL")
    assert gw.pages == before and gw.page_writes == writes


def test_remove_drops_a_route_to_a_view_under_pdk(gw: FakeGateway) -> None:
    assert pages.remove(gw, "/menu") == Outcome(True, "removed /menu -> PDK/Menu")
    assert "/menu" not in gw.pages and "/pdk/PDK/Menu" in gw.pages  # its preview page stays


def test_remove_refuses_a_route_to_a_view_outside_pdk(gw: FakeGateway) -> None:
    before, writes = dict(gw.pages), gw.page_writes
    assert pages.remove(gw, "/") == Outcome(False, "/ opens Home, outside PDK/: it is the project's; "
                                                   "change it in the Designer")
    assert gw.pages == before and gw.page_writes == writes


def test_remove_says_when_there_is_no_such_route(gw: FakeGateway) -> None:
    assert pages.remove(gw, "/nope") == Outcome(False, "no route /nope")


def test_cli_parses_the_three_actions() -> None:
    parser = build_parser()
    listed = parser.parse_args(["pages", "list"])
    added = parser.parse_args(["pages", "add", "/detail", "PDK/Detail", "--title", "Detail", "--url", "http://gw:8088"])
    removed = parser.parse_args(["pages", "remove", "/detail"])
    assert (listed.action, added.action, removed.action) == ("list", "add", "remove")
    assert (added.route, added.view, added.title, added.url) == ("/detail", "PDK/Detail", "Detail", "http://gw:8088")
    assert removed.route == "/detail" and removed.url is None


def test_cli_prints_the_outcome_and_exits_by_it(gw: FakeGateway, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr("pdk.gateway.client.Gateway", lambda url=None: gw)
    assert main(["pages", "list"]) == 0
    assert "+ 2 preview pages under /pdk/" in capsys.readouterr().out
    assert main(["pages", "add", "/pdk/x", "PDK/Detail"]) == 1
    assert main(["pages", "remove", "/nope"]) == 1
    assert "no route /nope" in capsys.readouterr().out


def test_cli_reports_a_gateway_error_on_stderr(gw: FakeGateway, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    gw.health = lambda: {"sandbox": None}  # the module is read-only: no sandbox to edit
    monkeypatch.setattr("pdk.gateway.client.Gateway", lambda url=None: gw)
    assert main(["pages", "list"]) == 1
    assert "pdk: " in capsys.readouterr().err


def test_pdk_pages_tool_is_a_non_destructive_signed_write() -> None:
    tool = {t.name: t for t in asyncio.run(mcp_server.server.list_tools())}["pdk_pages"]
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
    assert "/pdk/... is reserved for preview pages" in tool.description


def test_pdk_pages_tool_runs_the_actions_and_explains_a_bad_call(gw: FakeGateway,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_server, "Gateway", lambda: gw)
    assert mcp_server.pdk_pages("list").splitlines()[-1] == "+ 2 preview pages under /pdk/"
    assert mcp_server.pdk_pages("add", url="/detail", view="PDK/Detail") == "added /detail -> PDK/Detail"
    assert mcp_server.pdk_pages("remove", url="/detail") == "removed /detail -> PDK/Detail"
    assert mcp_server.pdk_pages("add", url="/detail").startswith("pdk: usage")
    assert mcp_server.pdk_pages("bogus").startswith("pdk: usage")
