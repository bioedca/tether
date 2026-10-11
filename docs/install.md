# Install Tether

**Who this page is for.** A lab scientist who wants to run Tether on their own Windows, macOS
or Linux computer. It assumes you can download a file and type a command into a terminal
window. It does **not** assume you know Python, conda or how software environments work: the
installer brings everything Tether needs, including its own Python, and nothing here asks you
to manage it.

If you have not decided yet whether Tether suits your experiment, read
[Does Tether fit my data?](compatibility.md) first. If you want to build Tether from a source
checkout or contribute to it, this is not your page; see
[Installers & offline bundle](packaging.md) and the repository's `CONTRIBUTING.md`.

The steps are: download one installer, check it, run it, put Tether's launcher folder on your
`PATH`, and confirm it answers. Uninstalling is covered at the end.

> **The current release is a pre-release.** The newest published release is
> [`v1.0.0-rc2`](https://github.com/bioedca/tether/releases/tag/v1.0.0-rc2), a release
> candidate for 1.0, and every filename on this page is read off it. Do not use the older
> `v1.0.0-rc1`: its one-click idealization fails with a `pkg_resources` error that rc2 fixes.

## 1. Download the installer for your computer

Open the [release page](https://github.com/bioedca/tether/releases/tag/v1.0.0-rc2), expand
**Assets**, and download two files: your installer and the checksum file that goes with it.
Put both in the same folder (your Downloads folder is fine).

| Your computer | Installer | Checksum file |
|---|---|---|
| Windows (64-bit) | `Tether-1.0.0-rc2-Windows-x86_64.exe` | `SHA256SUMS-win-64.txt` |
| Mac with Apple silicon (M1 or later) | `Tether-1.0.0-rc2-MacOSX-arm64.pkg` | `SHA256SUMS-osx-arm64.txt` |
| Mac with an Intel processor | `Tether-1.0.0-rc2-MacOSX-x86_64.pkg` | `SHA256SUMS-osx-64.txt` |
| Linux (64-bit x86) | `Tether-1.0.0-rc2-Linux-x86_64.sh` | `SHA256SUMS-linux-64.txt` |

On a Mac, **Apple menu → About This Mac** shows *Chip* (Apple silicon) or *Processor*
(Intel). Each installer is roughly 650–820 MB because it carries everything offline; nothing else is
downloaded while it installs.

## 2. Check the download

Tether's installers are **not code-signed by Apple or Microsoft**, and for 1.0 they will not
be ([ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)). There
are two checks, and they answer different questions.

The **checksum** tells you the download arrived whole: if the file on your disk produces the
SHA-256 value the release lists, nothing was corrupted or cut short on the way. It does not
tell you *who built* the file, because the checksum list comes from the same release page as
the installer, and anyone able to replace one could replace both. Open a terminal in the
folder holding both files and run the line for your system.

**Linux**

```bash
sha256sum -c SHA256SUMS-linux-64.txt
```

**macOS** (use `SHA256SUMS-osx-64.txt` on an Intel Mac)

```bash
shasum -a 256 -c SHA256SUMS-osx-arm64.txt
```

Both print the installer's name followed by `OK`. Anything else, such as `FAILED`, means the
file does not match: delete it and download it again.

**Windows** (in PowerShell, opened in the download folder)

```powershell
$expected = ((Get-Content .\SHA256SUMS-win-64.txt) -split '\s+')[0]
$actual = (Get-FileHash -Algorithm SHA256 `
  .\Tether-1.0.0-rc2-Windows-x86_64.exe).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" } else { "OK" }
```

It prints `OK`, or stops with `SHA-256 verification failed`.

The release also carries `SHA256SUMS.txt`, one file listing every asset. On Linux you can
check against it directly with `sha256sum -c --ignore-missing SHA256SUMS.txt`, which skips the
installers you did not download.

The **build-provenance attestation** is the check that the file is genuine. GitHub signs a
record, when the release is built, binding each installer to this repository, the release
workflow and the exact commit it was built from, and the check below compares your file
against that record. It needs the [GitHub command-line tool](https://cli.github.com/) (`gh`),
signed in to any GitHub account. Run it in the download folder with your installer's name:

```bash
gh attestation verify Tether-1.0.0-rc2-Linux-x86_64.sh \
  --repo bioedca/tether \
  --signer-workflow bioedca/tether/.github/workflows/release.yml \
  --source-ref refs/tags/v1.0.0-rc2 \
  --source-digest 292dd151c0263f981f8cd0f358d5e2074091d9de
```

In PowerShell, put the command on one line or end each continued line with a backtick
(`` ` ``) instead of `\`. A genuine installer reports that verification succeeded; anything
else means do not run it.

## 3. Run the installer

Because the installers are unsigned, Windows and macOS will warn you the first time you open
one. The warning is expected, and the attestation check above is what tells you the file is
genuine before you choose to go past it; the checksum alone cannot. What it looks like and how to
get past it on each system is in
[The installer is flagged as unsigned](troubleshooting.md#the-installer-is-flagged-as-unsigned).

No step needs an administrator account if you install for yourself only, which is what this
page assumes.

**Windows.** Double-click the `.exe`. When SmartScreen says *"Windows protected your PC"*,
choose **More info → Run anyway**. Accept the license, choose **Just Me**, and keep the
suggested destination folder unless you have a reason to change it. The steps below assume
`C:\Users\<you>\Tether`; if you chose another folder, use that path instead.

**macOS.** The most reliable route is one Terminal command, run in the download folder (use
the `x86_64` name on an Intel Mac):

```bash
installer -pkg Tether-1.0.0-rc2-MacOSX-arm64.pkg -target CurrentUserHomeDirectory
```

It installs into `~/Tether` (the `Tether` folder in your home folder) without asking for a
password. This is the exact command Tether's own macOS build check runs. You can also
double-click the `.pkg` and choose to install for yourself only; if macOS blocks it, use
**System Settings → Privacy & Security → Open Anyway** as the troubleshooting entry linked
above describes.

**Linux.** Run the installer with `sh`:

```bash
sh Tether-1.0.0-rc2-Linux-x86_64.sh
```

Page through the license, type `yes`, and press Enter to accept the suggested location,
`~/tether` (lower-case, in your home folder). The location **must not contain a space**; the
installer refuses one. Wait for `installation finished.`

Installing unpacks about 3.6 GB and took around a minute and a half on a Linux test machine.

## 4. Put Tether's launcher folder on your PATH

The installer creates a `bin` folder inside the install folder holding two launchers: `tether`
(the command-line tool) and `tether-gui` (the desktop application). It deliberately **does not
edit your PATH, shell settings or registry**, so a terminal cannot find those launchers until
you add that folder once. Run the line for your system, then **close the terminal and open a
new one**.

**Linux** (bash):

```bash
echo 'export PATH="$PATH:$HOME/tether/bin"' >> ~/.bashrc
```

**macOS** (zsh, the default Terminal shell):

```bash
echo 'export PATH="$PATH:$HOME/Tether/bin"' >> ~/.zshrc
```

**Windows** (PowerShell; this changes your own user PATH, not the system one):

```powershell
[Environment]::SetEnvironmentVariable("Path",
  [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\Tether\bin",
  "User")
```

The folder goes at the **end** of your PATH on purpose. On Linux and macOS it also holds the
installer's own copies of `python`, `pip` and `conda`; at the end, any you already use keep
working exactly as before.

## 5. Check that Tether runs

In the **new** terminal, run:

```bash
tether --version
```

It prints `tether` and the version, for this release:

```text
tether 1.0.0rc2
```

If it prints exactly that version, the install worked and your terminal finds its launcher.
If it prints a **different** version, an older Tether (for example a source checkout) comes
earlier on your `PATH` and is the one answering; remove that one from your `PATH`, or run the
new launcher by its full path, `~/tether/bin/tether --version` on Linux,
`~/Tether/bin/tether --version` on macOS or `& "$env:USERPROFILE\Tether\bin\tether.bat" --version` in
Windows PowerShell. To start the desktop application:

- **Windows:** open the Start menu and choose **Tether**, or run `tether-gui` in a terminal.
- **Linux:** choose **Tether** from your applications menu, or run `tether-gui` in a terminal.
  (A desktop that was already running may only show the menu entry after you log out and back
  in.)
- **macOS:** run `tether-gui` in Terminal. There is no Dock or Launchpad icon yet.

The application opens with no project loaded; use **File → Open project…** to open one. The
[Command line](cli.md) reference covers what `tether` itself can do.

## What gets installed, and where

Everything lives in the single folder you chose (`~/tether` on Linux, `~/Tether` on macOS,
`C:\Users\<you>\Tether` on Windows by default):

| Inside the install folder | What it is |
|---|---|
| `bin/tether`, `bin/tether-gui` (on Windows, `bin\tether.bat`, `bin\tether-gui.bat`) | The two launchers this page puts on your PATH. |
| `envs/tether` | The Tether application and everything it runs on. |
| `envs/sidecar` | A separate, isolated copy of tMAVEN used for one-click idealization. It is kept apart because it needs older library versions than the application. |
| everything else | The small installer toolkit used once, at install time, to lay down the two folders above. It holds no Tether code. |

Outside the install folder the installer creates exactly one thing: on Windows a **Tether**
Start menu shortcut, and on Linux an application-menu entry at
`~/.local/share/applications/tether.desktop`. On macOS it creates nothing outside `~/Tether`.

On a Linux test install of this release the folder came to **3.6 GB**; the other systems have
not been measured yet.

## If it did not work

**`tether: command not found`, or *"is not recognized as the name of a cmdlet"*.** The
terminal cannot see the launcher folder. Check that you opened a **new** terminal after step
4, and that the folder in the PATH line matches where you installed.

**The application does not open.** Tether does not write a log file, so start it from a
terminal with `tether-gui` and read what it prints. On Windows the Start menu shortcut and
`tether-gui` show no console, so run this instead to see the error text:

```powershell
& "$env:USERPROFILE\Tether\envs\tether\python.exe" -m tether.gui
```

**Linux: *"Could not load the Qt platform plugin "xcb""*.** The full message looks like this:

```text
qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt xcb platform plugin.
qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.
This application failed to start because no Qt platform plugin could be initialized. Reinstalling the application may fix this problem.
```

Reinstalling will not fix it. Two causes produce it. Either there is no graphical desktop
(a server, or an SSH session without display forwarding), in which case use the `tether`
command line instead, which needs no display; or the system is missing a library the
installer cannot ship, which on Debian and Ubuntu you add with
`sudo apt install libxcb-cursor0` and on Fedora with `sudo dnf install xcb-util-cursor`.

Everything else, keyed by the message you saw, is in [Troubleshooting](troubleshooting.md).

## Uninstall

Tether keeps to its one folder, so removing it is mostly deleting that folder. Then remove
the PATH line you added in step 4.

> **Check the folder first.** Deleting the install folder deletes *everything* in it. If you
> ever saved a `.tether` project, a movie or an export inside it, move those out before you
> uninstall; nothing below asks before deleting them.

**Linux**

```bash
rm -rf ~/tether ~/.local/share/applications/tether.desktop
```

Then delete the `export PATH=…/tether/bin` line from `~/.bashrc`.

**macOS**

```bash
rm -rf ~/Tether
```

Then delete the `export PATH=…/Tether/bin` line from `~/.zshrc`.

**Windows.** Open **Settings → Apps → Installed apps**, find **Tether**, and choose
**Uninstall**. If a **Tether** folder is still in the Start menu afterwards, delete it. Remove
`C:\Users\<you>\Tether\bin` from your user PATH under **Settings → System → About → Advanced
system settings → Environment Variables**.

Projects, movies and exports saved anywhere **outside** the install folder are not touched
by any of these steps.

## The optional GPU add-on

The deep trace classifier is an optional extra for users with an NVIDIA GPU who want to train
or run a deep model on their own labels. It is **not** part of the installer and Tether does
not need it; setting it up is described on [Deep trace classifier](ml/deep-classifier.md).
