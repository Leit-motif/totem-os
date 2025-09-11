import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class TotemAPIHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Totem API placeholder: hello")

    def do_POST(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Totem API placeholder: hello")


def main() -> None:
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("", port), TotemAPIHandler)
    print(f"Totem API running on port {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
