from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import subprocess
import sys

SAVE_FOLDER = "received_audio"
os.makedirs(SAVE_FOLDER, exist_ok=True)

class FogHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        if self.path != "/upload":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers["Content-Length"])
        filename = self.headers.get("X-Filename", "audio.wav")

        data = self.rfile.read(content_length)

        filepath = os.path.join(SAVE_FOLDER, filename)

        with open(filepath, "wb") as f:
            f.write(data)

        print(f"Received and saved: {filepath}")

        # Launch analyze.py on the saved file
        try:
            subprocess.Popen([sys.executable, "analyze.py", filepath])
            print(f"Started analyze.py for {filepath}")
        except Exception as e:
            print(f"Failed to start analyze.py: {e}")

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Upload successful")

if __name__ == "__main__":
    host = "0.0.0.0"   # listen on all interfaces
    port = 8000

    print(f"Fog server running on port {port}...")
    HTTPServer((host, port), FogHandler).serve_forever()
