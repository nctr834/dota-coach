from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import threading
import time


class GSIHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers["Content-Length"])
        post_data = json.loads(self.rfile.read(content_length))
        print(json.dumps(post_data, indent=2))
        self.send_response(200)
        self.end_headers()


def start_gsi_server(port: int = 6000):
    server = HTTPServer(("127.0.0.1", port), GSIHandler)
    server.serve_forever()


if __name__ == "__main__":
    start_gsi_server()
