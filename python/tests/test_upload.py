"""upload_file: the token walk to the API host only (I1)."""
import gzip
import json
import os
import random
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from datagoat import Client, DatagoatError
from datagoat import cli
from datagoat.client import PIECE_MAX_BYTES, binary_format


class Fake:
    """The API's side of the walk: dg_add_dataset(upload: true), then the pieces, then complete.
    It keeps what a piece carried the way the server does: the header on every piece, the rows in
    order, a repeated piece ignored."""

    def __init__(self, pieces_host=None, fail_first=(), redirect_to=None):
        self.requests = []   # (method, path, headers)
        self.header = None
        self.rows = []       # the bytes of each landed piece after its header
        self.session = None
        self.completed = None
        self.types = set()   # the content-types the pieces came with
        self.fail_first = set(fail_first)
        self.redirect_to = redirect_to
        self.pieces_host = pieces_host
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body, headers=()):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                fake.requests.append(("GET", self.path, dict(self.headers)))
                self.reply(404, {})

            def do_PUT(self):
                fake.requests.append(("PUT", self.path, dict(self.headers)))
                self.reply(500, {})

            def do_POST(self):
                n = int(self.headers.get("content-length", 0))
                body = self.rfile.read(n)
                fake.requests.append(("POST", self.path, dict(self.headers)))
                if self.path == "/v1/add-dataset":
                    host = fake.pieces_host or f"http://127.0.0.1:{self.server.server_port}"
                    return self.reply(200, {"dataset_id": "ds_" + "a" * 25, "status": "awaiting_upload", "rows": None, "columns": None,
                                            "upload_url": "http://127.0.0.1:9/presigned", "upload_method": "PUT", "expires_in": 3600,
                                            "upload_page": "https://datagoat.io/upload/v1.tok.mac", "upload_page_expires_in": 3600,
                                            "upload_pieces_url": f"{host}/v1/uploads/v1.tok.mac"})
                assert self.path.startswith("/v1/uploads/v1.tok.mac/"), self.path
                idx = self.path.rsplit("/", 1)[1]
                if fake.redirect_to:
                    return self.reply(302, {}, [("location", fake.redirect_to + self.path)])
                if idx in fake.fail_first:
                    fake.fail_first.discard(idx)
                    return self.reply(503, {"code": "engine_unavailable", "detail": "d", "remedy": "r"})
                session = self.headers.get("x-upload-session")
                assert session and len(session) == 32
                if idx == "complete":
                    assert session == fake.session
                    fake.completed = json.loads(body)["pieces"]
                    assert fake.completed == len(fake.rows)
                    return self.reply(200, {"dataset_id": "ds_" + "a" * 25, "status": "open", "pieces": len(fake.rows)})
                i = int(idx)
                assert len(body) <= PIECE_MAX_BYTES
                assert self.headers.get("authorization") is None, "the link is the credential; no key travels with a piece"
                binary = self.headers.get("content-type") == "application/octet-stream"
                fake.types.add(self.headers.get("content-type"))
                head, _, rest = (b"", b"", body) if binary else body.partition(b"\n")
                if i == 0:
                    fake.session, fake.header = session, head
                assert session == fake.session and head == fake.header
                if i < len(fake.rows):
                    return self.reply(200, {"pieces": len(fake.rows)})  # a repeat is a no-op
                assert i == len(fake.rows), "pieces arrive in order"
                fake.rows.append(rest)
                return self.reply(200, {"dataset_id": "ds_" + "a" * 25, "status": "awaiting_upload", "pieces": len(fake.rows)})

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def only_the_api(monkeypatch):
    """An agent sandbox: the one reachable host is the API. Any other connection fails."""
    allowed = set()
    real = socket.create_connection

    def guarded(address, *a, **k):
        if (address[0], int(address[1])) not in allowed:
            raise ConnectionRefusedError(f"egress to {address} is blocked")
        return real(address, *a, **k)
    monkeypatch.setattr(socket, "create_connection", guarded)
    return allowed


def big_csv(path, target_bytes):
    rng = random.Random(7)
    with open(path, "wb") as f:
        f.write(b"customer_id,plan,note,seats,churned\r\n")
        size, i = 0, 0
        while size < target_bytes:
            line = ('c%07d,%s,"said ""hi""\nthen left, maybe",%d,%d\r\n' % (
                rng.randrange(10**7), rng.choice(["basic", "pro", "équipe"]), rng.randrange(1, 500), i % 3 == 0)).encode()
            f.write(line)
            size += len(line)
            i += 1
    return os.path.getsize(path)


def test_a_15_mb_file_uploads_with_only_the_api_reachable(tmp_path, only_the_api):
    fake = Fake(fail_first={"1"})
    only_the_api.add(("127.0.0.1", fake.srv.server_port))
    path = tmp_path / "big.csv"
    size = big_csv(path, 15_500_000)
    assert size >= 15_000_000
    seen = []
    dg = Client(api_key="dgk_live_x", base_url=fake.url)
    dg._sleep = lambda s: None
    ds = dg.upload_file(str(path), on_piece=seen.append)
    assert ds == "ds_" + "a" * 25
    assert fake.completed == len(fake.rows) >= 5 and seen == list(range(1, len(fake.rows) + 1))
    raw = path.read_bytes()
    head, _, body = raw.partition(b"\r\n")
    assert fake.header == head
    assert b"".join(fake.rows) == body, "the pieces carry the file's rows, whole and in order"
    assert all(m == "POST" for m, _, _ in fake.requests), "nothing is PUT to the storage host"
    paths = [p for _, p, _ in fake.requests]
    assert paths[0] == "/v1/add-dataset" and paths[-1] == "/v1/uploads/v1.tok.mac/complete"
    assert paths.count("/v1/uploads/v1.tok.mac/1") == 2, "the piece that failed was sent again"
    fake.close()


def test_gzip_and_parquet_go_as_byte_slices_joined_verbatim(tmp_path, only_the_api):
    """I4: a .csv.gz or Parquet file is recognised by its bytes and sent as it is; the engine reads it."""
    csv = tmp_path / "big.csv"
    big_csv(csv, 9_000_000)
    gz = tmp_path / "big.csv.gz"
    gz.write_bytes(gzip.compress(csv.read_bytes(), compresslevel=0))
    pq = tmp_path / "t.parquet"
    pq.write_bytes(b"PAR1" + bytes(range(256)) * 20 + struct.pack("<I", 8) + b"PAR1")
    for path in (gz, pq):
        fake = Fake()
        only_the_api.add(("127.0.0.1", fake.srv.server_port))
        assert Client(api_key="dgk_live_x", base_url=fake.url).upload_file(str(path)) == "ds_" + "a" * 25
        assert b"".join(fake.rows) == path.read_bytes(), "the pieces are the file, byte for byte"
        assert fake.types == {"application/octet-stream"}
        assert fake.completed == len(fake.rows)
        fake.close()
    assert binary_format(str(csv)) is None and binary_format(str(gz)) == "gzip" and binary_format(str(pq)) == "parquet"


def test_the_pieces_go_to_the_clients_own_host_even_when_the_link_names_another(tmp_path, only_the_api):
    fake = Fake(pieces_host="https://elsewhere.example")
    only_the_api.add(("127.0.0.1", fake.srv.server_port))
    path = tmp_path / "small file.csv"
    path.write_bytes(b"id,y\n1,0\n2,1\n")
    dg = Client(api_key="dgk_live_x", base_url=fake.url)
    assert dg.upload_file(str(path)) == "ds_" + "a" * 25
    assert b"".join(fake.rows) == b"1,0\n2,1\n"
    assert json.loads(json.dumps(fake.requests[0][2])).get("Content-Type") == "application/json"
    fake.close()


def test_a_redirect_is_refused_and_nothing_follows_it(tmp_path, only_the_api):
    other = Fake()
    fake = Fake(redirect_to=other.url)
    only_the_api.update({("127.0.0.1", fake.srv.server_port), ("127.0.0.1", other.srv.server_port)})
    path = tmp_path / "a.csv"
    path.write_bytes(b"id,y\n1,0\n")
    with pytest.raises(DatagoatError) as e:
        Client(api_key="dgk_live_x", base_url=fake.url).upload_file(str(path))
    assert e.value.code == "upload_redirect_refused"
    assert other.requests == [], "no byte reached the host the redirect named"
    fake.close()
    other.close()


def test_presigned_is_opt_in_and_bad_files_are_refused_before_sending(tmp_path):
    fake = Fake()
    dg = Client(api_key="dgk_live_x", base_url=fake.url)
    empty = tmp_path / "e.csv"
    empty.write_bytes(b"id,y\n")
    with pytest.raises(ValueError, match="header row and at least one row"):
        dg.upload_file(str(empty))
    latin = tmp_path / "l.csv"
    latin.write_bytes("id,name\n1,Zo\xeb\n".encode("latin-1"))
    with pytest.raises(ValueError, match="UTF-8"):
        dg.upload_file(str(latin))
    with pytest.raises(ValueError, match="transport"):
        dg.upload_file(str(latin), transport="s3")
    assert not [r for r in fake.requests if r[1].startswith("/v1/uploads/")]
    fake.close()


def test_cli_upload_prints_the_dataset_id(monkeypatch, capsys, tmp_path):
    calls = []

    class FakeClient:
        def upload_file(self, path, transport="pieces", on_piece=None):
            calls.append((path, transport))
            on_piece(1)
            return "ds_x"
    monkeypatch.setattr(cli, "Client", lambda *a, **k: FakeClient())
    assert cli.main(["upload", "data.csv"]) == 0
    assert capsys.readouterr().out.strip() == "ds_x"
    assert cli.main(["upload", "data.csv", "--presigned"]) == 0
    assert calls == [("data.csv", "pieces"), ("data.csv", "presigned")]
