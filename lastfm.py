#!/usr/bin/env python


def parse_url(url, use_proxy=False):
    import requests
    import lxml.etree as etree
    import io
    kwargs = {}
    if use_proxy:
        kwargs["proxies"] = {
            "http" : "http://localhost:8123",
            "https" : "http://localhost:8123",
        }
    r = requests.get(url, **kwargs)
    content = io.StringIO(r.text)
    return etree.parse(content, etree.HTMLParser())


def get_recommendation(url):
    tree = parse_url(url, use_proxy=True)
    track_elems = tree.xpath(
        '//section/ol[contains(@class, "similar-tracks")]/li')

    def elem_to_track(elem):
        [title, artist] = elem.xpath('*//a[@itemprop]/text()')
        return (title, artist)

    return list(map(elem_to_track, track_elems))


def get_track_url_from_user_list(user: str):
    # import ptpython.repl
    # ptpython.repl.embed(locals(), globals())
    tree = parse_url(f'https://www.last.fm/user/{user}', use_proxy=True)
    path = tree.xpath('//section[1]/table/tbody[1]/tr[1]/td[4]/a/@href')[0]
    return f'https://last.fm{path}'


def search_netease(title, artist, api):
    # Read tracks and convert to internal format
    result = api.search(f"{title} - {artist}", 1, 0, 10)
    try:
        tracks = result['result']['songs']
        tracks = [{
            'id': track['id'],
            'title': track['name'],
            'artist': '/'.join(artist['name'] for artist in track['ar']),
            'album': track['al']['name'],
        } for track in tracks]
    except (KeyError, TypeError):
        print(result)
        return

    matches = {
        track['id']
        for track in tracks if track['title'].casefold() == title.casefold()
        and track['artist'].casefold() == artist.casefold()
    }
    # Directly add if only one item presents.
    if len(matches) == 1:
        tid = matches.pop()
        for track in tracks:
            if track['id'] == tid:
                print(
                    f"!! {track['id']}: {track['title']} - {track['album']} - {track['artist']}"
                )
        return tid

    for track in tracks:
        symbol = '✓' if track['id'] in matches else '×'
        print(
            f"{symbol} {track['id']}: {track['title']} - {track['album']} - {track['artist']}"
        )
    return None


def url_to_recommendation(url, api):
    import time
    import os
    count = int(os.environ.get('COUNT', '5'))
    print(f"Getting recommendation from {url} with limit {count}")

    tids = []
    recommendation = get_recommendation(url)
    for (title, artist) in recommendation[:count]:
        print(f"==== {title} - {artist} ==== ")
        tid = search_netease(title, artist, api)
        if tid is not None:
            tids.append(tid)
        time.sleep(5)
    return tids


def main():
    from sys import argv
    from api import NeteaseAPI

    api = NeteaseAPI()
    api.load_cookie("cookies")

    cmd = argv[1]
    if cmd == "autourl":
        url = get_track_url_from_user_list('hghwng')
        tids = url_to_recommendation(url, api)
    elif cmd == "url":
        tids = url_to_recommendation(argv[2], api)
    elif cmd == "direct":
        tids = [int(i) for i in argv[2:]]

    if tids:
        print("Adding tracks to playlist: ", tids)
        api.manipulate_playlist_tracks(4868922696, tids, 'add')
    api.dump_cookie("cookies")


if __name__ == '__main__':
    main()
