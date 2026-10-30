#!/usr/bin/env python3
"""
WordPress Folder Publisher - simple GUI.
Prompts for WordPress credentials and an article folder, uploads local images,
rewrites image URLs, and creates a WordPress post.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import threading
import mimetypes
import json
import base64
import tempfile
from pathlib import Path
from urllib.parse import urlparse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import webbrowser

APP_NAME = "WordPress Folder Publisher"
TIMEOUT = 90
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".bmp", ".tif", ".tiff"}

def ensure_dependencies():
    needed = []
    if importlib.util.find_spec("requests") is None:
        needed.append("requests")
    if importlib.util.find_spec("bs4") is None:
        needed.append("beautifulsoup4")
    if needed:
        # Install only the missing runtime dependencies automatically.
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", *needed])
        except Exception as exc:
            raise RuntimeError(
                "Could not install required Python packages: " + ", ".join(needed) +
                "\\nPython executable: " + sys.executable +
                "\\nCheck internet access, proxy/firewall settings, and whether pip is available. " +
                "You can also run: " + sys.executable + " -m pip install --user requests beautifulsoup4" +
                "\\nOriginal error: " + str(exc)
            ) from exc

ensure_dependencies()
import requests
from bs4 import BeautifulSoup

def api_url(site_url: str, endpoint: str) -> str:
    return site_url.rstrip("/") + "/wp-json/wp/v2/" + endpoint.lstrip("/")

def local_image_path(src: str, html_path: Path, root: Path):
    src = (src or "").strip()
    if not src or src.startswith(("data:", "blob:", "#", "//")):
        return None
    parsed = urlparse(src)
    if parsed.scheme in ("http", "https"):
        return None
    cleaned = parsed.path
    candidates = [(html_path.parent / cleaned).resolve(), (root / cleaned.lstrip("/")).resolve()]
    for candidate in candidates:
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file() and candidate.suffix.lower() in IMAGE_EXTENSIONS:
            return candidate
    return None

def infer_title(soup, html_path):
    h1 = soup.find("h1")
    if h1 and h1.get_text(" ", strip=True):
        return h1.get_text(" ", strip=True)
    if soup.title and soup.title.get_text(" ", strip=True):
        return soup.title.get_text(" ", strip=True)
    return html_path.stem.replace("-", " ").replace("_", " ").strip().title()

def find_html_files(folder: Path):
    # Prefer article-like HTML and avoid common navigation/helper pages when possible.
    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in (".html", ".htm"))
    preferred = [p for p in files if p.name.lower() not in ("index.html", "readme.html")]
    return preferred or files

def upload_media(session, site_url, image_path: Path):
    mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    safe_name = image_path.name.replace('"', "")
    with image_path.open("rb") as stream:
        response = session.post(
            api_url(site_url, "media"),
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"', "Content-Type": mime},
            data=stream, timeout=TIMEOUT
        )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Image upload failed for {image_path.name} (HTTP {response.status_code}):\\n{response.text[:600]}")
    return response.json()


# Keep settings in the same folder as the application files.
# This works for an extracted ZIP folder; the folder must be writable.
APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = APP_DIR
CONFIG_FILE = CONFIG_DIR / "publisher_settings.json"

def _powershell_executable() -> str:
    """Find Windows PowerShell without relying on PATH."""
    candidates = [
        Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(os.environ.get("SystemRoot", r"C:\\Windows")) / "SysWOW64" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    # Last resort: use PATH lookup if available.
    import shutil
    found = shutil.which("powershell.exe") or shutil.which("powershell")
    if found:
        return found
    raise FileNotFoundError(
        "Windows PowerShell could not be found. Expected it under %SystemRoot%\\System32\\WindowsPowerShell\\v1.0."
    )

def _dpapi_protect(text: str) -> str:
    """Encrypt a secret using Windows DPAPI for the current Windows user."""
    if os.name != "nt":
        return text
    ps_script = (
        "Add-Type -AssemblyName System.Security; "
        "$inputText = [Console]::In.ReadToEnd(); "
        "$bytes = [Text.Encoding]::UTF8.GetBytes($inputText); "
        "$enc = [Security.Cryptography.ProtectedData]::Protect($bytes, $null, "
        "[Security.Cryptography.DataProtectionScope]::CurrentUser); "
        "[Console]::Out.Write([Convert]::ToBase64String($enc))"
    )
    result = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", ps_script],
        input=text, capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        raise RuntimeError("Windows could not encrypt the Application Password with DPAPI.")
    return "dpapi:" + result.stdout.strip()

def _dpapi_unprotect(value: str) -> str:
    if not value.startswith("dpapi:") or os.name != "nt":
        return value
    encoded = value[6:]
    ps_script = (
        "Add-Type -AssemblyName System.Security; "
        "$inputText = [Console]::In.ReadToEnd(); "
        "$enc = [Convert]::FromBase64String($inputText); "
        "$bytes = [Security.Cryptography.ProtectedData]::Unprotect($enc, $null, "
        "[Security.Cryptography.DataProtectionScope]::CurrentUser); "
        "[Console]::Out.Write([Text.Encoding]::UTF8.GetString($bytes))"
    )
    result = subprocess.run(
        [_powershell_executable(), "-NoProfile", "-NonInteractive", "-Command", ps_script],
        input=encoded, capture_output=True, text=True, timeout=15
    )
    if result.returncode != 0:
        raise RuntimeError("Windows could not decrypt the saved Application Password.")
    return result.stdout

def load_settings():
    try:
        if not CONFIG_FILE.exists():
            return {}
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        saved_secret = data.pop("application_password", "")
        if saved_secret:
            try:
                data["application_password"] = _dpapi_unprotect(saved_secret)
            except Exception:
                data["application_password"] = ""
        return data
    except Exception:
        return {}

def save_settings(data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    saved = dict(data)
    secret = saved.pop("application_password", "")
    if secret:
        saved["application_password"] = _dpapi_protect(secret)
    CONFIG_FILE.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("800x680")
        self.minsize(720, 600)
        self.folder = tk.StringVar()
        self.site = tk.StringVar(value="https://cadcamcaeworks.com")
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.html_choice = tk.StringVar()
        self.status = tk.StringVar(value="draft")
        self.title_override = tk.StringVar()
        self.excerpt = tk.StringVar()
        self.show_password = tk.BooleanVar(value=False)
        self.html_files = []
        self.busy = False
        self._build()
        self._load_saved_fields()

    def _build(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="WordPress Folder Publisher", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Select an article folder. The app will find its HTML, upload local images, and create the post.").pack(anchor="w", pady=(2, 12))

        cred = ttk.LabelFrame(outer, text="WordPress publishing account", padding=10)
        cred.pack(fill="x", pady=(0, 10))
        self._field(cred, "Website URL", self.site, 0)
        self._field(cred, "WordPress username", self.username, 1)
        ttk.Label(cred, text="Application Password").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        self.password_entry = ttk.Entry(cred, textvariable=self.password, show="•", width=52)
        self.password_entry.grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Checkbutton(cred, text="Show", variable=self.show_password, command=self._toggle_password).grid(row=2, column=2, padx=6)
        cred.columnconfigure(1, weight=1)
        ttk.Label(cred, text="Use a WordPress Application Password, not your normal account password.", foreground="#555").grid(row=3, column=1, sticky="w", pady=(0, 3))

        folder_frame = ttk.LabelFrame(outer, text="Article folder", padding=10)
        folder_frame.pack(fill="x", pady=(0, 10))
        ttk.Entry(folder_frame, textvariable=self.folder).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(folder_frame, text="Browse…", command=self._browse).grid(row=0, column=1)
        folder_frame.columnconfigure(0, weight=1)

        options = ttk.LabelFrame(outer, text="Post settings", padding=10)
        options.pack(fill="x", pady=(0, 10))
        ttk.Label(options, text="HTML article file").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.html_combo = ttk.Combobox(options, textvariable=self.html_choice, state="readonly", width=48)
        self.html_combo.grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(options, text="Detect HTML", command=self._detect_html).grid(row=0, column=2, padx=(8, 0))
        ttk.Label(options, text="Post title override (optional)").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(options, textvariable=self.title_override).grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(options, text="Excerpt (optional)").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(options, textvariable=self.excerpt).grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)
        ttk.Label(options, text="Publish mode").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        mode = ttk.Frame(options)
        mode.grid(row=3, column=1, columnspan=2, sticky="w")
        ttk.Radiobutton(mode, text="Save as draft (recommended)", value="draft", variable=self.status).pack(side="left", padx=(0, 12))
        ttk.Radiobutton(mode, text="Publish immediately", value="publish", variable=self.status).pack(side="left")
        options.columnconfigure(1, weight=1)

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 8))
        self.run_button = ttk.Button(buttons, text="Connect, upload images & create post", command=self._start)
        self.run_button.pack(side="left")
        ttk.Button(buttons, text="Clear log", command=self._clear_log).pack(side="left", padx=8)
        ttk.Button(buttons, text="Clear saved details", command=self._clear_saved_details).pack(side="left", padx=8)
        ttk.Button(buttons, text="Help", command=self._help).pack(side="right")

        log_frame = ttk.LabelFrame(outer, text="Progress and results", padding=6)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=12, wrap="word", state="disabled")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._log("Ready. Your credentials are used for this run and are not saved by this app.")


    def _load_saved_fields(self):
        data = load_settings()
        self.site.set(data.get("site_url", self.site.get()))
        self.username.set(data.get("username", ""))
        self.password.set(data.get("application_password", ""))
        self.folder.set(data.get("folder", ""))
        self.html_choice.set(data.get("html_file", ""))
        self.status.set(data.get("status", "draft"))
        self.title_override.set(data.get("title_override", ""))
        self.excerpt.set(data.get("excerpt", ""))
        if self.folder.get() and Path(self.folder.get()).is_dir():
            self._detect_html()
            saved_html = data.get("html_file", "")
            if saved_html in self.html_combo["values"]:
                self.html_choice.set(saved_html)
        self._log("Loaded saved preferences." if data else "No saved preferences found.")

    def _save_current_fields(self):
        data = {
            "site_url": self.site.get().strip(),
            "username": self.username.get().strip(),
            "application_password": self.password.get().strip(),
            "folder": self.folder.get().strip().strip('"'),
            "html_file": self.html_choice.get().strip(),
            "status": self.status.get(),
            "title_override": self.title_override.get().strip(),
            "excerpt": self.excerpt.get().strip(),
        }
        save_settings(data)
        self._log(f"Saved preferences to: {CONFIG_FILE}")

    def _clear_saved_details(self):
        if not messagebox.askyesno(
            "Clear saved details",
            "This will delete the saved website, username, Application Password, folder, HTML selection, and post settings from this Windows user profile. Continue?"
        ):
            return
        try:
            if CONFIG_FILE.exists():
                CONFIG_FILE.unlink()
            try:
                if CONFIG_DIR.exists() and not any(CONFIG_DIR.iterdir()):
                    CONFIG_DIR.rmdir()
            except Exception:
                pass
            self.site.set("https://cadcamcaeworks.com")
            self.username.set("")
            self.password.set("")
            self.folder.set("")
            self.html_choice.set("")
            self.status.set("draft")
            self.title_override.set("")
            self.excerpt.set("")
            self.html_combo["values"] = ()
            self.html_files = []
            self._log("Saved details cleared.")
            messagebox.showinfo("Cleared", "Saved details were deleted.")
        except Exception as exc:
            messagebox.showerror("Could not clear saved details", str(exc))

    def _field(self, parent, label, var, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
        ttk.Entry(parent, textvariable=var, width=52).grid(row=row, column=1, sticky="ew", pady=5)

    def _toggle_password(self):
        self.password_entry.configure(show="" if self.show_password.get() else "•")

    def _browse(self):
        chosen = filedialog.askdirectory(title="Select article folder")
        if chosen:
            self.folder.set(chosen)
            self._detect_html()

    def _detect_html(self):
        raw = self.folder.get().strip().strip('"')
        if not raw or not Path(raw).is_dir():
            messagebox.showwarning("Select folder", "Please select a valid article folder first.")
            return
        self.html_files = find_html_files(Path(raw).resolve())
        values = [str(p.relative_to(Path(raw).resolve())) for p in self.html_files]
        self.html_combo["values"] = values
        if values:
            self.html_choice.set(values[0] if len(values) == 1 else (self.html_choice.get() if self.html_choice.get() in values else values[0]))
            self._log(f"Found {len(values)} HTML file(s). Selected: {self.html_choice.get()}")
        else:
            self.html_choice.set("")
            self._log("No HTML files found in this folder or its subfolders.")

    def _log(self, message):
        self.after(0, self._append_log, message)

    def _append_log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _help(self):
        messagebox.showinfo("Setup help",
            "1. In WordPress, open Users → Profile → Application Passwords and create a new password for this app.\\n"
            "2. Enter your site URL, username, and that Application Password here.\\n"
            "3. Select the article folder containing the HTML and image files.\\n"
            "4. Choose Draft or Publish immediately. Draft is safer.\\n\\n"
            "The account must be allowed to create posts and upload media. Your site must allow WordPress REST API access over HTTPS.\\n"
            "Preferences are saved locally. On Windows, the Application Password is encrypted using Windows DPAPI for your Windows user account. Use Clear saved details to remove them.\\n"
            "This app does not build Elementor layouts; it creates a standard WordPress post from the HTML content.")

    def _start(self):
        if self.busy:
            return
        site = self.site.get().strip().rstrip("/")
        folder = Path(self.folder.get().strip().strip('"')).expanduser()
        user = self.username.get().strip()
        password = self.password.get().strip().replace(" ", "")
        if not site.startswith(("https://", "http://")):
            messagebox.showerror("Website URL", "Enter a full website URL, such as https://example.com")
            return
        if not user or not password:
            messagebox.showerror("Credentials required", "Enter your WordPress username and Application Password.")
            return
        if not folder.is_dir():
            messagebox.showerror("Article folder", "Choose a valid article folder.")
            return
        self._detect_html()
        if not self.html_choice.get():
            messagebox.showerror("HTML not found", "No HTML file was found in the selected folder.")
            return
        selected = (folder.resolve() / self.html_choice.get()).resolve()
        try:
            selected.relative_to(folder.resolve())
        except ValueError:
            messagebox.showerror("Invalid HTML path", "The selected HTML file must be inside the article folder.")
            return
        if not selected.is_file():
            messagebox.showerror("HTML not found", "Selected HTML file could not be found.")
            return
        if self.status.get() == "publish":
            ok = messagebox.askyesno("Publish immediately?", "This will publish the post publicly as soon as the upload completes. Continue?")
            if not ok:
                return
        try:
            self._save_current_fields()
        except Exception as exc:
            if not messagebox.askyesno(
                "Could not save preferences",
                f"The app could not save preferences securely:\\n{exc}\\n\\nContinue this run without saving them?"
            ):
                return
        self.busy = True
        self.run_button.configure(state="disabled", text="Working…")
        args = (site, folder.resolve(), selected, user, password, self.status.get(), self.title_override.get().strip(), self.excerpt.get().strip())
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _worker(self, site, folder, html_path, username, password, status, title_override, excerpt):
        try:
            self._log(f"Reading article: {html_path.relative_to(folder)}")
            html_text = html_path.read_text(encoding="utf-8-sig")
            soup = BeautifulSoup(html_text, "html.parser")
            title = title_override or infer_title(soup, html_path)
            content_node = soup.body if soup.body else soup
            image_nodes = content_node.find_all("img")
            image_paths = {}
            missing = set()
            for img in image_nodes:
                src = img.get("src", "")
                path = local_image_path(src, html_path, folder)
                if path:
                    image_paths[path] = None
                elif src and not src.startswith(("http://", "https://", "data:", "blob:", "//", "#")):
                    missing.add(src)
            self._log(f"Post title: {title}")
            self._log(f"Found {len(image_paths)} local image(s) referenced by the article.")
            for path in sorted(image_paths):
                self._log(f"  Image: {path.relative_to(folder)}")
            for src in sorted(missing):
                self._log(f"WARNING: local image path not found: {src}")
            session = requests.Session()
            session.auth = (username, password)
            session.headers.update({"User-Agent": "WordPress-Folder-Publisher/1.0"})
            self._log("Checking WordPress connection and account permissions…")
            response = session.get(api_url(site, "users/me"), timeout=TIMEOUT)
            if response.status_code != 200:
                raise RuntimeError(f"WordPress authentication/API check failed (HTTP {response.status_code}). Check URL, username, Application Password and REST API access.\\n{response.text[:500]}")
            user_data = response.json()
            self._log(f"Connected as: {user_data.get('name') or user_data.get('slug') or user_data.get('id')}")
            for path in list(image_paths):
                self._log(f"Uploading {path.name}…")
                image_paths[path] = upload_media(session, site, path)
                self._log(f"Uploaded: {path.name}")
            for img in image_nodes:
                path = local_image_path(img.get("src", ""), html_path, folder)
                if path and image_paths.get(path):
                    media = image_paths[path]
                    img["src"] = media.get("source_url", img.get("src"))
                    img["data-wp-media-id"] = str(media.get("id", ""))
                    if img.has_attr("srcset"):
                        del img["srcset"]
            content = "".join(str(child) for child in content_node.contents).strip()
            payload = {"title": title, "content": content, "status": status}
            if excerpt:
                payload["excerpt"] = excerpt
            self._log(f"Creating WordPress post as {status}…")
            response = session.post(api_url(site, "posts"), json=payload, timeout=TIMEOUT)
            if response.status_code not in (200, 201):
                raise RuntimeError(f"Post creation failed (HTTP {response.status_code}):\\n{response.text[:1000]}")
            post = response.json()
            link = post.get("link", "")
            self._log("SUCCESS: WordPress post created.")
            self._log(f"Title: {post.get('title', {}).get('rendered', title)}")
            self._log(f"Status: {post.get('status', status)}")
            self._log(f"Post ID: {post.get('id')}")
            if link:
                self._log(f"Post URL / preview: {link}")
                self.after(0, lambda u=link: self._show_success(u, post.get("status", status)))
            else:
                self.after(0, lambda: messagebox.showinfo("Success", "Post created successfully."))
        except Exception as exc:
            self._log("ERROR: " + str(exc))
            self.after(0, lambda e=str(exc): messagebox.showerror("Publishing failed", e))
        finally:
            self.after(0, self._finish)

    def _show_success(self, url, status):
        msg = f"Post created as {status}.\\n\\n{url}"
        if status == "publish":
            if messagebox.askyesno("Published", msg + "\\n\\nOpen the post now?"):
                webbrowser.open(url)
        else:
            if messagebox.askyesno("Draft created", msg + "\\n\\nOpen the preview/editor link now?"):
                webbrowser.open(url)

    def _finish(self):
        self.busy = False
        self.run_button.configure(state="normal", text="Connect, upload images & create post")

if __name__ == "__main__":
    try:
        App().mainloop()
    except Exception as exc:
        messagebox.showerror(APP_NAME, f"The app could not start.\\n\\n{exc}")
