#!/usr/bin/env python
import base64
import json
import requests
from Crypto.Cipher import AES


class NeteaseAPI:
    _AES_OBJ = AES.new(bytes('rFgB&h#%2?^eDg:Q', 'UTF-8'), AES.MODE_ECB)
    _API_URL = 'http://music.163.com/api/linux/forward'

    def __init__(self):
        self.req = requests.Session()
        self.req.headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36' \
                                         ' (KHTML, like Gecko) Chrome/47.0.2526.80 Safari/537.36'
        self.req.headers['Origin'] = 'orpheus://orpheus'
        self.req.cookies['os'] = 'linux';

    @staticmethod
    def decrypt(data: str) -> dict:
        text = NeteaseAPI._AES_OBJ.decrypt(base64.b16decode(data))
        pad_length = text[-1]
        text = text[:-pad_length]
        return json.loads(str(text, 'UTF-8'))

    @staticmethod
    def encrypt(data: dict) -> str:
        data_bytes = bytes(json.dumps(data), 'UTF-8')
        pad_length = (len(data_bytes) // 16 + 1) * 16 - len(data_bytes)
        data_bytes += bytes((pad_length,)) * pad_length
        return base64.b16encode(NeteaseAPI._AES_OBJ.encrypt(data_bytes))

    def request(self, url, params, method='POST'):
        payload_dict = dict(url=url, method=method, params=params)
        payload_bytes = NeteaseAPI.encrypt(payload_dict)
        response = self.req.post(NeteaseAPI._API_URL, {'eparams': payload_bytes})
        return json.loads(response.text)

    def dump_cookie(self, path):
        import pickle
        pickle.dump(self.req.cookies, open(path, 'wb'))

    def load_cookie(self, path):
        import pickle
        self.req.cookies = pickle.load(open(path, 'rb'))

    def login_cellphone(self, phone, password):
        import hashlib
        URL = 'http://music.163.com/api/login/cellphone'
        md5sum = hashlib.md5(bytes(password, 'UTF-8')).hexdigest()
        payload = dict(password=md5sum, https="true", remember="true", phone=phone, type=1)
        return self.request(URL, payload)

    def get_user_playlist(self, uid, limit=1000, offset=0):
        URL = 'http://music.163.com/api/user/playlist/'
        return self.request(URL, dict(uid=uid, limit=limit, offset=offset))

    def get_playlist_detail(self, pid):
        URL = 'http://music.163.com/api/v3/playlist/detail'
        return self.request(URL, dict(id=pid, n=0, t=-1, s=0))

    def get_track_detail(self, tids):
        URL = 'http://music.163.com/api/v3/song/detail'
        c = [dict(id=t) for t in tids]
        return self.request(URL, dict(c=json.dumps(c)))

    def get_player_url(self, tids, bitrate='999000'):
        URL = 'http://music.163.com/api/song/enhance/player/url'
        return self.request(URL, dict(br=bitrate, ids=json.dumps(tids)))

    def get_download_url(self, tid, bitrate='999000'):
        URL = 'http://music.163.com/api/song/enhance/download/url'
        return self.request(URL, dict(br=bitrate, id=tid))

    def like_track(self, tid):
        URL = 'http://music.163.com/api/song/like'
        return self.request(URL, dict(userid=0, trackId=tid, like=True))

    def manipulate_playlist_tracks(self, pid, tids, op):
        ''' op: add/del '''
        URL = 'http://music.163.com/api/playlist/manipulate/tracks'
        return self.request(URL, dict(pid=pid, trackIds=tids, op=op))

    def get_radio(self):
        URL = 'http://music.163.com/api/v1/radio/get'
        return self.request(URL, dict())

    def trash_radio(self, tid, mode='trash', time=0, alg='alternate'):
        SKIP_URL = 'http://music.163.com/api/v1/radio/skip'
        TRASH_URL = 'http://music.163.com/api/radio/trash/add'
        URL = TRASH_URL if mode == 'trash' else SKIP_URL
        return self.request(URL, dict(alg=alg, songId=str(tid), time=str(time)))

    def do_daily_task(self, type_):
        URL = 'http://music.163.com/api/point/dailyTask'
        return self.request(URL, dict(type=type_))


class NeteaseApiCli(NeteaseAPI):
    def __init__(self, cookies="cookies"):
        super(NeteaseApiCli, self).__init__()
        import os
        if os.path.isfile(cookies):
            self.load_cookie(cookies)
        self._cookies = cookies

    def __del__(self):
        self.dump_cookie(self._cookies)

    def decrypt(self, data):
        return super(NeteaseApiCli, self).decrypt(data)

    def get_player_url(self, *tids, bitrate=3200000):
        return super(NeteaseApiCli, self).get_player_url(list(map(int, tids)), bitrate)

    def get_download_url(self, tid, bitrate=3200000):
        return super(NeteaseApiCli, self).get_download_url(int(tid), bitrate)

    def add_songs_to_list(self, pid, *tids):
        return self.manipulate_playlist_tracks(pid, list(map(int, tids)), 'add')

    def delete_songs_from_list(self, pid, *tids):
        return self.manipulate_playlist_tracks(pid, list(map(int, tids)), 'del')

    def skip_radio(self, tid):
        return self.trash_radio(tid, mode='skip')


def main():
    import fire
    fire.Fire(NeteaseApiCli)


if __name__ == '__main__':
    main()
