"""Saved stations are found even when Alexa mis-hears the name."""

import asyncio
from types import SimpleNamespace

import pytest

from fork_ext import voice_search
from fork_ext.voice_search import SearchPlaybackError, find_media

OXIGENIO = SimpleNamespace(uri="library://radio/10", name="OXIGÉNIO 102.6 FM")
M80 = SimpleNamespace(uri="library://radio/3", name="M80 rádio")
FUTURA = SimpleNamespace(uri="library://radio/11", name="FUTURA - Rádio de Autor")
ANTENA3 = SimpleNamespace(uri="library://radio/4", name="Antena 3 (Portugal)")
SAVED = [OXIGENIO, M80, FUTURA, ANTENA3]


def _client(saved=SAVED):
    calls = {"global": 0}

    async def search(**kwargs):
        if not kwargs.get("library_only"):
            calls["global"] += 1
        return SimpleNamespace(tracks=[], artists=[], radio=[])

    async def get_library_radios(**kwargs):
        return list(saved)

    music = SimpleNamespace(search=search, get_library_radios=get_library_radios)
    return SimpleNamespace(music=music), calls


@pytest.mark.parametrize("query", [
    "rádio oxigêneo", "rádio oxigeno", "oxigenio", "radio oxigênio",
])
def test_track_intent_finds_misheard_saved_station_without_global_search(query):
    client, calls = _client()
    assert asyncio.run(find_media(client, query, "track")) is OXIGENIO
    assert calls["global"] == 0


@pytest.mark.parametrize("query", ["oxigeno", "rádio oxigêneo"])
def test_radio_intent_finds_misheard_saved_station(query):
    client, _ = _client()
    assert asyncio.run(find_media(client, query, "radio")) is OXIGENIO


@pytest.mark.parametrize("query", ["jungle", "futuro", "antena 1", "xyz"])
def test_unrelated_names_do_not_pick_a_station(query):
    client, calls = _client()
    with pytest.raises(SearchPlaybackError):
        asyncio.run(find_media(client, query, "track"))
    assert calls["global"] >= 1


def test_two_equally_similar_stations_are_not_guessed():
    twins = [SimpleNamespace(uri="library://radio/1", name="Rádio Solar"),
             SimpleNamespace(uri="library://radio/2", name="Rádio Polar")]
    client, _ = _client(saved=twins)
    with pytest.raises(SearchPlaybackError):
        asyncio.run(find_media(client, "rádio molar", "radio"))


def test_library_listing_failure_falls_back_to_normal_search():
    async def search(**kwargs):
        return SimpleNamespace(tracks=[], artists=[], radio=[])

    async def broken(**kwargs):
        raise RuntimeError("boom")

    client = SimpleNamespace(music=SimpleNamespace(search=search, get_library_radios=broken))
    with pytest.raises(SearchPlaybackError):
        asyncio.run(find_media(client, "oxigeno", "track"))


@pytest.mark.parametrize(("name", "key"), [
    ("OXIGÉNIO 102.6 FM", "oxigenio"),
    ("M80 rádio", "m80"),
    ("Antena 3 (Portugal)", "antena 3 portugal"),
    ("rádio oxigêneo", "oxigeneo"),
])
def test_radio_key(name, key):
    assert voice_search._radio_key(name) == key
