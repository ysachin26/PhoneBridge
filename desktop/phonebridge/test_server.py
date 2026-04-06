"""
PhoneBridge — Test WebDAV Server

A minimal WebDAV server that simulates an Android phone running PhoneBridge.
Used for testing the PC tray app without needing a real phone.

Usage:
    python -m phonebridge.test_server

This will:
1. Start a WebDAV server on port 8273 serving a test directory
2. Register an mDNS service so the PC app can discover it
3. Create some test files to browse

Press Ctrl+C to stop.
"""

import os
import sys
import time
import socket
import logging
import threading
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, quote
import xml.etree.ElementTree as ET

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_server")

# Constants
PORT = 8273
SERVICE_TYPE = "_phonebridge._tcp.local."
DEVICE_NAME = "Test Phone (Simulator)"
DEVICE_MODEL = "PhoneBridge-Test"


def get_local_ip() -> str:
    """Get the local IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class WebDAVHandler(BaseHTTPRequestHandler):
    """Minimal WebDAV request handler for testing."""

    # Root directory to serve
    root_dir: Path = Path(".")

    def log_message(self, format, *args):
        logger.info(f"{self.client_address[0]} - {format % args}")

    def do_OPTIONS(self):
        """Handle OPTIONS (WebDAV capability discovery)."""
        self.send_response(200)
        self.send_header("Allow", "OPTIONS, GET, PUT, DELETE, MKCOL, PROPFIND, MOVE, COPY")
        self.send_header("DAV", "1, 2")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PROPFIND(self):
        """Handle PROPFIND (directory listing)."""
        path = self._resolve_path()
        depth = self.headers.get("Depth", "1")

        logger.debug(f"PROPFIND {self.path} (depth={depth})")

        if not path.exists():
            self.send_error(404, "Not Found")
            return

        # Build multistatus XML response
        responses = []

        if path.is_file():
            responses.append(self._file_response(path))
        else:
            # Directory itself
            responses.append(self._dir_response(path))

            # Children (if depth > 0)
            if depth != "0":
                try:
                    for child in sorted(path.iterdir()):
                        if child.name.startswith("."):
                            continue
                        if child.is_dir():
                            responses.append(self._dir_response(child))
                        else:
                            responses.append(self._file_response(child))
                except PermissionError:
                    pass

        body = self._build_multistatus(responses)
        body_bytes = body.encode("utf-8")

        self.send_response(207)
        self.send_header("Content-Type", 'application/xml; charset="utf-8"')
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        """Handle GET (file download)."""
        path = self._resolve_path()

        if not path.exists():
            self.send_error(404, "Not Found")
            return

        if path.is_dir():
            # Return a simple HTML directory listing
            self._serve_directory_html(path)
            return

        # Serve file
        try:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", self._guess_mime(path))
            self.send_header("Content-Length", str(size))
            self.end_headers()

            with open(path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            logger.error(f"Error serving file: {e}")
            self.send_error(500, str(e))

    def do_PUT(self):
        """Handle PUT (file upload)."""
        path = self._resolve_path()

        # Create parent directories
        path.parent.mkdir(parents=True, exist_ok=True)

        content_length = int(self.headers.get("Content-Length", 0))
        try:
            with open(path, "wb") as f:
                remaining = content_length
                while remaining > 0:
                    chunk_size = min(remaining, 65536)
                    chunk = self.rfile.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)

            self.send_response(201)
            self.send_header("Content-Length", "0")
            self.end_headers()
            logger.info(f"File uploaded: {path} ({content_length} bytes)")
        except Exception as e:
            logger.error(f"Upload error: {e}")
            self.send_error(500, str(e))

    def do_DELETE(self):
        """Handle DELETE (file/dir removal)."""
        path = self._resolve_path()

        if not path.exists():
            self.send_error(404, "Not Found")
            return

        try:
            if path.is_dir():
                import shutil
                shutil.rmtree(path)
            else:
                path.unlink()

            self.send_response(204)
            self.end_headers()
            logger.info(f"Deleted: {path}")
        except Exception as e:
            logger.error(f"Delete error: {e}")
            self.send_error(500, str(e))

    def do_MKCOL(self):
        """Handle MKCOL (create directory)."""
        path = self._resolve_path()

        if path.exists():
            self.send_error(405, "Already exists")
            return

        try:
            path.mkdir(parents=True, exist_ok=True)
            self.send_response(201)
            self.end_headers()
            logger.info(f"Directory created: {path}")
        except Exception as e:
            logger.error(f"MKCOL error: {e}")
            self.send_error(500, str(e))

    def do_MOVE(self):
        """Handle MOVE (rename/move file)."""
        src = self._resolve_path()
        dest_header = self.headers.get("Destination", "")

        if not src.exists():
            self.send_error(404, "Source not found")
            return

        if not dest_header:
            self.send_error(400, "No Destination header")
            return

        # Parse destination path from URL
        from urllib.parse import urlparse
        dest_uri = urlparse(dest_header).path
        dest = self.root_dir / unquote(dest_uri).lstrip("/")

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest)
            self.send_response(201)
            self.end_headers()
            logger.info(f"Moved: {src} → {dest}")
        except Exception as e:
            logger.error(f"Move error: {e}")
            self.send_error(500, str(e))

    # ─── Helpers ──────────────────────────────────────────────

    def _resolve_path(self) -> Path:
        """Convert request URI to local filesystem path."""
        uri = unquote(self.path).lstrip("/")
        # Security: prevent path traversal
        resolved = (self.root_dir / uri).resolve()
        if not str(resolved).startswith(str(self.root_dir.resolve())):
            return self.root_dir  # Fallback to root
        return resolved

    def _file_response(self, path: Path) -> str:
        """Generate a DAV response element for a file."""
        stat = path.stat()
        rel = "/" + str(path.relative_to(self.root_dir)).replace("\\", "/")
        href = quote(rel)
        size = stat.st_size
        modified = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(stat.st_mtime))

        return f"""<D:response>
  <D:href>{href}</D:href>
  <D:propstat>
    <D:prop>
      <D:displayname>{path.name}</D:displayname>
      <D:getcontentlength>{size}</D:getcontentlength>
      <D:getlastmodified>{modified}</D:getlastmodified>
      <D:getcontenttype>{self._guess_mime(path)}</D:getcontenttype>
      <D:resourcetype/>
    </D:prop>
    <D:status>HTTP/1.1 200 OK</D:status>
  </D:propstat>
</D:response>"""

    def _dir_response(self, path: Path) -> str:
        """Generate a DAV response element for a directory."""
        rel = "/" + str(path.relative_to(self.root_dir)).replace("\\", "/")
        if not rel.endswith("/"):
            rel += "/"
        href = quote(rel)
        stat = path.stat()
        modified = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(stat.st_mtime))

        return f"""<D:response>
  <D:href>{href}</D:href>
  <D:propstat>
    <D:prop>
      <D:displayname>{path.name}</D:displayname>
      <D:getlastmodified>{modified}</D:getlastmodified>
      <D:resourcetype><D:collection/></D:resourcetype>
    </D:prop>
    <D:status>HTTP/1.1 200 OK</D:status>
  </D:propstat>
</D:response>"""

    def _build_multistatus(self, responses: list[str]) -> str:
        """Build the complete multistatus XML response."""
        body = '<?xml version="1.0" encoding="utf-8" ?>\n'
        body += '<D:multistatus xmlns:D="DAV:">\n'
        body += "\n".join(responses)
        body += "\n</D:multistatus>"
        return body

    def _serve_directory_html(self, path: Path):
        """Serve a simple HTML directory listing for browser access."""
        rel = "/" + str(path.relative_to(self.root_dir)).replace("\\", "/")
        html = f"<html><head><title>{rel}</title></head><body>"
        html += f"<h1>📁 {rel}</h1><ul>"

        if path != self.root_dir:
            html += f'<li><a href="..">⬆️ ..</a></li>'

        for child in sorted(path.iterdir()):
            if child.name.startswith("."):
                continue
            icon = "📁" if child.is_dir() else "📄"
            name = child.name + ("/" if child.is_dir() else "")
            href = quote(child.name) + ("/" if child.is_dir() else "")
            size = "" if child.is_dir() else f" ({child.stat().st_size:,} bytes)"
            html += f'<li>{icon} <a href="{href}">{name}</a>{size}</li>'

        html += "</ul></body></html>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode())))
        self.end_headers()
        self.wfile.write(html.encode())

    @staticmethod
    def _guess_mime(path: Path) -> str:
        """Guess MIME type from file extension."""
        ext = path.suffix.lower()
        mimes = {
            ".txt": "text/plain",
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".xml": "application/xml",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".svg": "image/svg+xml",
            ".mp4": "video/mp4",
            ".mp3": "audio/mpeg",
            ".pdf": "application/pdf",
            ".zip": "application/zip",
            ".apk": "application/vnd.android.package-archive",
        }
        return mimes.get(ext, "application/octet-stream")


def create_test_files(root: Path):
    """Create sample test files to browse."""
    logger.info(f"Creating test files in: {root}")

    # Photos directory
    photos = root / "DCIM" / "Camera"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "photo_2025_vacation.txt").write_text("This simulates a photo file (vacation)")
    (photos / "photo_2025_family.txt").write_text("This simulates a photo file (family)")
    (photos / "selfie_001.txt").write_text("This simulates a selfie")

    # Downloads
    downloads = root / "Download"
    downloads.mkdir(parents=True, exist_ok=True)
    (downloads / "document.pdf.txt").write_text("This simulates a PDF document")
    (downloads / "presentation.pptx.txt").write_text("This simulates a PowerPoint file")

    # Music
    music = root / "Music"
    music.mkdir(parents=True, exist_ok=True)
    (music / "song_favourite.txt").write_text("This simulates an MP3 file")

    # Documents
    docs = root / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "notes.txt").write_text("Some important notes\nLine 2\nLine 3")
    (docs / "todo.txt").write_text("1. Build PhoneBridge\n2. Test it\n3. Release it")

    # WhatsApp
    wa = root / "WhatsApp" / "Media" / "Images"
    wa.mkdir(parents=True, exist_ok=True)
    (wa / "meme_funny.txt").write_text("This simulates a WhatsApp image")

    logger.info(f"Created {sum(1 for _ in root.rglob('*') if _.is_file())} test files")


def register_mdns(ip: str, port: int):
    """Register the test server as an mDNS service."""
    try:
        from zeroconf import Zeroconf, ServiceInfo
        import socket

        zc = Zeroconf()
        info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=f"{DEVICE_NAME}.{SERVICE_TYPE}",
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={
                "version": "1",
                "deviceName": DEVICE_NAME,
                "model": DEVICE_MODEL,
            },
        )
        zc.register_service(info)
        logger.info(f"✅ mDNS registered: {DEVICE_NAME} on {ip}:{port}")
        return zc, info
    except ImportError:
        logger.warning("zeroconf not installed — mDNS registration skipped")
        logger.warning("Install with: pip install zeroconf")
        return None, None
    except Exception as e:
        logger.error(f"mDNS registration failed: {e}")
        return None, None


def main():
    """Run the test WebDAV server."""
    # Create test directory
    test_root = Path(__file__).parent.parent / "test_phone_storage"
    test_root.mkdir(exist_ok=True)
    create_test_files(test_root)

    # Configure handler
    WebDAVHandler.root_dir = test_root

    # Get local IP
    local_ip = get_local_ip()

    # Start HTTP server
    server = HTTPServer(("0.0.0.0", PORT), WebDAVHandler)
    logger.info(f"🚀 WebDAV server running on http://{local_ip}:{PORT}")
    logger.info(f"   Serving: {test_root.resolve()}")

    # Register mDNS service
    zc, svc_info = register_mdns(local_ip, PORT)

    print()
    print("=" * 55)
    print(f"  📱 Test Phone Server Ready!")
    print(f"  🌐 WebDAV URL: http://{local_ip}:{PORT}")
    print(f"  📁 Root: {test_root.resolve()}")
    print(f"  🔍 mDNS: {'✅ Active' if zc else '❌ Disabled'}")
    print("=" * 55)
    print("  Press Ctrl+C to stop")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.shutdown()
        if zc and svc_info:
            zc.unregister_service(svc_info)
            zc.close()
        logger.info("Server stopped")


if __name__ == "__main__":
    main()
