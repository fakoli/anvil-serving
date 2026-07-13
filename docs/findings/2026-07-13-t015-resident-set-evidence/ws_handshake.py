import socket, base64, os, hashlib, sys, json

host, port = "127.0.0.1", 8765
paths = ["/", "/ws", "/v1/realtime", "/realtime"]
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

def attempt(path):
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        "GET %s HTTP/1.1\r\n"
        "Host: %s:%d\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    ) % (path, host, port, key)
    s = socket.create_connection((host, port), timeout=8)
    s.settimeout(8)
    s.sendall(req.encode())
    data = b""
    while b"\r\n\r\n" not in data and len(data) < 4096:
        chunk = s.recv(1024)
        if not chunk:
            break
        data += chunk
    s.close()
    text = data.decode("latin1", "replace")
    status = text.split("\r\n", 1)[0]
    headers = {}
    for line in text.split("\r\n")[1:]:
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    expected = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    accept = headers.get("sec-websocket-accept")
    ok = status.startswith("HTTP/1.1 101") and accept == expected
    return {"path": path, "status_line": status, "accept_ok": (accept == expected),
            "sec_websocket_accept": accept, "expected_accept": expected,
            "upgrade": headers.get("upgrade"), "handshake_ok": ok}

results = []
final = None
for p in paths:
    try:
        r = attempt(p)
    except Exception as e:
        r = {"path": p, "error": repr(e)}
    results.append(r)
    if r.get("handshake_ok"):
        final = r
        break

out = {"target": "ws://127.0.0.1:8765 on %s" % socket.gethostname(),
       "handshake_ok": bool(final), "successful_path": final["path"] if final else None,
       "attempts": results}
print(json.dumps(out, indent=2))
sys.exit(0 if final else 3)
