#!/usr/bin/env python
import base64
import json
import requests
from Crypto.Cipher import AES

class NeteaseAPI:
    AES_OBJ = AES.new(bytes('rFgB&h#%2?^eDg:Q', 'UTF-8'))
    API_URL = 'http://music.163.com/api/linux/forward'

    def __init__(self):
        self.req = requests.Session()
        self.req.headers['User-Agent'] = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36' \
                                         ' (KHTML, like Gecko) Chrome/47.0.2526.80 Safari/537.36'
        self.req.headers['Origin'] = 'orpheus://orpheus'

    @staticmethod
    def decrypt(data: str) -> dict:
        text = NeteaseAPI.AES_OBJ.decrypt(base64.b16decode(data))
        pad_length = text[-1]
        return json.loads(str(text[:-pad_length], 'UTF-8'))

    @staticmethod
    def encrypt(data: dict) -> str:
        data_bytes = bytes(json.dumps(data), 'UTF-8')
        pad_length = (len(data_bytes) // 16 + 1) * 16 - len(data_bytes)
        data_bytes += bytes((pad_length,)) * pad_length
        return base64.b16encode(NeteaseAPI.AES_OBJ.encrypt(data_bytes))

    def request(self, url, params, method='POST'):
        payload_dict = dict(url=url, method=method, params=params)
        payload_bytes = NeteaseAPI.encrypt(payload_dict)
        response = self.req.post(NeteaseAPI.API_URL, {'eparams': payload_bytes})
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
        payload = dict(password=md5sum, https=True, remember=True, phone=phone, type=1)
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

    def get_player_url(self, tids, bitrate='320000'):
        URL = 'http://music.163.com/api/song/enhance/player/url'
        return self.request(URL, dict(br=bitrate, ids=json.dumps(tids)))

    def like_track(self, tid):
        URL = 'http://music.163.com/api/song/like'
        return self.request(URL, dict(userid=0, trackId=tid, like=True))

    def manipulate_playlist_tracks(self, pid, tids, op):
        ''' op: add/del '''
        URL = 'http://music.163.com/api/playlist/manipulate/tracks'
        return self.request(URL, dict(pid=pid, trackIds=tids, op=op))


def main():
    from sys import argv
    import os
    api = NeteaseAPI()
    if os.path.isfile('cookies'):
        api.load_cookie('cookies')

    result = None
    if argv[1] == 'l':
        result = api.login_cellphone(argv[2], argv[3])
    elif argv[1] == 'up':
        result = api.get_user_playlist(int(argv[2]))
    elif argv[1] == 'pd':
        result = api.get_playlist_detail(int(argv[2]))
    elif argv[1] == 'td':
        result = api.get_track_detail((int(argv[2]), ))
    elif argv[1] == 'pu':
        result = api.get_player_url((int(argv[2]), ))
    elif argv[1] == 'li':
        result = api.like_track(int(argv[2]))
    elif argv[1] == 'mta':
        result = api.manipulate_playlist_tracks(int(argv[2]), (int(argv[3]),), 'add')
    elif argv[1] == 'mtd':
        result = api.manipulate_playlist_tracks(int(argv[2]), (int(argv[3]),), 'del')
    elif argv[1] == 'd':
        result = NeteaseAPI.decrypt(input())
    else:
        print('Invalid')

    import pprint
    pprint.pprint(result)
    api.dump_cookie('cookies')


if __name__ == '__main__':
    main()
