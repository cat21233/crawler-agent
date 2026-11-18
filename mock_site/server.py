import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import functools

BASE = os.path.dirname(os.path.abspath(__file__))
LIMIT = int(os.environ.get("MOCK_LIMIT", "0"))
# 绑哪个地址由环境变量决定，默认值保持本机开发的老行为（127.0.0.1:8099）。
# 容器里必须传 MOCK_HOST=0.0.0.0：绑 127.0.0.1 时只有容器自己能访问，
# 宿主机映射端口和 app 容器的 http://mock:8099 都会打不通。
HOST = os.environ.get("MOCK_HOST", "127.0.0.1")
PORT = int(os.environ.get("MOCK_PORT", "8099"))
_hits = 0


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        """静音父类默认日志，终端只留 [mock] 那一行。"""
        pass

    def do_GET(self):
        global _hits
        path = self.path.split("?")[0]

        if path == "/__reset":
            _hits = 0
            body = b"reset ok, hits=0\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain;charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            print("[mock] #0 reset", flush=True)
            return
            # ② 计数
        # 静态资源不占配额：浏览器加载一个页面会连带请求 css/ico/图片，
        # 全算进去的话一次打开就吃掉 3 次额度（实测踩过）。
        if path.lower().endswith((".css", ".js", ".ico", ".png", ".jpg", ".svg", ".woff2")):
            super().do_GET()
            return



        _hits += 1

        # ③ 超限 → 自己发 429 并结束，不调 super()
        if LIMIT > 0 and _hits > LIMIT:

            body = f"429 Too Many Requests (limit={LIMIT})\n".encode("utf-8")
            self.send_response(429)
            self.send_header("Retry-After", "5")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            print(f"[mock] #{_hits} {path} -> 429", flush=True)
            return

        # ④ 放行 → 交还给父类照常服务静态文件
        print(f"[mock] #{_hits} {path} -> 200", flush=True)
        super().do_GET()
def main():
    handler = functools.partial(Handler, directory=BASE)
    httpd = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"[mock] 起在 http://{HOST}:{PORT}  "
        f"MOCK_LIMIT={LIMIT or '不限流'}  BASE={BASE}",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock] 停", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()