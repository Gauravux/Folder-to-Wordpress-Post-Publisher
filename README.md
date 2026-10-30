# Folder-to-Wordpress-Post-Publisher
This python app allows you to post html articles saved in your local drive to Wordpress using application password.
# WordPress Folder Publisher (GUI)

## What it does
- Opens a desktop GUI; no environment variables or command-line arguments are needed.
- Asks for your WordPress site URL, publishing username, and **Application Password**.
- Lets you select the folder containing an HTML article and its images.
- Detects HTML files (including in subfolders); you can choose the article HTML.
- Uploads local images referenced by `<img src="...">`, replaces local paths with WordPress media URLs, and creates a standard WordPress post.
- Creates a **Draft by default**. You can explicitly choose “Publish immediately” and confirm.
- Credentials are used in memory for that run and are not written to a configuration file by this app.

## Run on Windows
1. Install Python 3.10+ from https://www.python.org/downloads/ if Python is not already installed. During installation, enable **Add Python to PATH**.
2. Extract this ZIP to a normal folder. The launcher tries the Python launcher, the PATH command, and your common per-user Python 3.13 installation path.
3. Double-click `Run_WordPress_Publisher.bat`.
4. On first launch, the app automatically installs the two Python dependencies it needs (`requests` and `beautifulsoup4`). Internet access may be required for this one-time setup. If startup fails, the launcher displays the diagnostic output and saves it to `publisher_startup.log` in the extracted folder.
5. Enter:
   - Website URL, e.g. `https://cadcamcaeworks.com`
   - WordPress username for the publishing account
   - WordPress **Application Password**
6. Select the article folder. Pick the correct HTML file if more than one is detected.
7. Choose Draft (recommended) or Publish immediately.

## Create an Application Password
In WordPress, go to **Users → Profile → Application Passwords**, add a label such as `Folder Publisher`, and generate a password. Copy the generated password into the app. Do not enter your normal WordPress login password. Your WordPress host/security plugins must permit REST API access.

## Important limitations
- Produces a normal WordPress post, not an Elementor-designed page/template.
- Uploads local images referenced by HTML `<img src>`. It does not upload CSS background images, JavaScript-created images, or other non-HTML assets.
- HTML/CSS styling may be sanitized by WordPress or altered by the active theme. Check the draft before publishing.
- Existing remote image URLs are left unchanged.
- It does not automatically create categories/tags or set a featured image.
- It has not been tested against your live WordPress site; host settings or plugins may block REST API media uploads.

## Security
Use a dedicated WordPress account with only the permissions needed to create posts and upload media. Revoke the Application Password in WordPress when no longer needed. The app does not save the password, but your computer and the running Python process still need to handle it.


## Remember previous settings
This version saves the website URL, username, Application Password, last article folder, selected HTML file, publish mode, title override, and excerpt locally after you start a publishing run. Settings are restored when you next open the app.

On Windows, the Application Password is encrypted using Windows DPAPI and is decryptable only by the same Windows user on that computer. Other settings are stored in:
`%APPDATA%\WordPressFolderPublisher\settings.json`

Use **Clear saved details** in the app to delete saved preferences and clear the form. This does not revoke the Application Password inside WordPress; to revoke it, go to WordPress Users → Profile → Application Passwords.


## Settings file location
Saved preferences are stored beside the application files in `publisher_settings.json`. The application folder must be writable by your Windows account. If you extract the app under a protected location such as `C:\\Program Files`, move it to a normal writable folder (for example, Documents or Desktop). The Application Password remains encrypted with Windows DPAPI.


## Fix for Windows PowerShell path errors
This build locates Windows PowerShell using its standard full installation path instead of relying only on the PATH environment variable. This addresses a Windows `[WinError 2]` that can occur when saving the encrypted Application Password on systems where PowerShell is not on PATH.
