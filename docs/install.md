---
description: "Download, verify, install and uninstall the Tether desktop installer on Windows, macOS or Linux. No Python or conda knowledge is necessary."
---

# Install Tether

**Who this page is for.** A lab scientist who wants to run Tether on their own Windows, macOS
or Linux computer. It assumes you can download a file and type a command into a terminal
window. It does **not** assume you know Python, conda or how software environments work. The
installer brings everything Tether needs, including its own Python. Nothing here asks you to
manage it.

If you have not decided yet whether Tether suits your experiment, read
[Does Tether fit my data?](compatibility.md) first. This page is not for you if you want to
build Tether from a source checkout or contribute to it. For that, read
[Installers & offline bundle](packaging.md) and the repository's `CONTRIBUTING.md`.

The steps are: download one installer, check it, run it, open a terminal where `tether` works,
and confirm it answers. Uninstalling is covered at the end.

> **The current release is a pre-release.** The newest published release is
> [`v1.0.0-rc3`](https://github.com/bioedca/tether/releases/tag/v1.0.0-rc3), a release
> candidate for 1.0. Every filename on this page is read off it. Do not use `v1.0.0-rc1`.
> Its one-click idealization fails with a `pkg_resources` error that rc2 fixes.
>
> Do not use `v1.0.0-rc2` on Windows. It creates no Start menu shortcut, and its launchers can crash when
> Tether does a one-click idealize or an extract. The rc3 release fixes both. On Linux and
> macOS, rc2 still works, but rc3 is the release this page describes.

## 1. Download the installer for your computer

1. Open the [release page](https://github.com/bioedca/tether/releases/tag/v1.0.0-rc3).
2. Expand **Assets**.
3. Download two files: your installer and the checksum file that goes with it. The table below
   names them.
4. Put both files in the same folder. Your Downloads folder is fine.

| Your computer | Installer | Checksum file |
|---|---|---|
| Windows (64-bit) | `Tether-1.0.0-rc3-Windows-x86_64.exe` | `SHA256SUMS-win-64.txt` |
| Mac with Apple silicon (M1 or later) | `Tether-1.0.0-rc3-MacOSX-arm64.pkg` | `SHA256SUMS-osx-arm64.txt` |
| Mac with an Intel processor | `Tether-1.0.0-rc3-MacOSX-x86_64.pkg` | `SHA256SUMS-osx-64.txt` |
| Linux (64-bit x86) | `Tether-1.0.0-rc3-Linux-x86_64.sh` | `SHA256SUMS-linux-64.txt` |

On a Mac, **Apple menu → About This Mac** shows *Chip* (Apple silicon) or *Processor*
(Intel). Each installer is roughly 660–830 MB because it carries everything offline. Nothing
else is downloaded while it installs.

## 2. Check the download

Tether's installers are **not code-signed by Apple or Microsoft**, and for 1.0 they will not
be ([ADR-0059](adr/0059-ship-v1-unsigned-with-provenance-as-the-integrity-anchor.md)). There
are two checks, and they answer different questions.

The **checksum** tells you that the download arrived whole. If the file on your disk gives the
SHA-256 value the release lists, nothing was corrupted or cut short. The checksum does not
tell you *who built* the file. The checksum list comes from the same release page as the
installer. Anyone able to replace
one could replace both.

Open a terminal in the folder that holds both files. Then run the line for your system.

**Linux**

```bash
sha256sum -c SHA256SUMS-linux-64.txt
```

**macOS** (use `SHA256SUMS-osx-64.txt` on an Intel Mac)

```bash
shasum -a 256 -c SHA256SUMS-osx-arm64.txt
```

Both print the installer's name followed by `OK`. Anything else, such as `FAILED`, means that
the file does not match. Delete it and download it again.

**Windows** (in PowerShell, opened in the download folder)

```powershell
$expected = ((Get-Content .\SHA256SUMS-win-64.txt) -split '\s+')[0]
$actual = (Get-FileHash -Algorithm SHA256 `
  .\Tether-1.0.0-rc3-Windows-x86_64.exe).Hash.ToLowerInvariant()
if ($actual -ne $expected) { throw "SHA-256 verification failed" } else { "OK" }
```

It prints `OK`, or stops with `SHA-256 verification failed`.

The release also carries `SHA256SUMS.txt`, one file that lists every asset. On Linux, you can
check against it directly with `sha256sum -c --ignore-missing SHA256SUMS.txt`. This command
skips the installers that you did not download.

The **build-provenance attestation** is the check that the file is genuine. When the release is
built, GitHub signs a record. The record binds each installer to this repository, the release
workflow and the exact commit it was built from. The check below compares your file against
that record. It needs the [GitHub command-line tool](https://cli.github.com/) (`gh`), signed in
to any GitHub account. Run it in the download folder with your installer's name:

```bash
gh attestation verify Tether-1.0.0-rc3-Linux-x86_64.sh \
  --repo bioedca/tether \
  --signer-workflow bioedca/tether/.github/workflows/release.yml \
  --source-ref refs/tags/v1.0.0-rc3 \
  --source-digest 6de7824fa73bebd70f8fbf6dda037b6857fd1b2f
```

In PowerShell, put the command on one line. Or end each continued line with a backtick
(`` ` ``) instead of `\`. A genuine installer reports that verification succeeded. If it
reports anything else, do not run the installer.

## 3. Run the installer

Because the installers are unsigned, Windows and macOS will warn you the first time you open
one. The warning is expected. The attestation check above tells you that the file is genuine
before you go past the warning. The checksum alone cannot tell you that.
[The installer is flagged as unsigned](troubleshooting.md#the-installer-is-flagged-as-unsigned)
shows the warning on each system and how to get past it.
[Why the installers are unsigned](#why-the-installers-are-unsigned) is at the end of this step.

This page assumes that you install for yourself only. That needs no administrator account on
Windows or Linux. On macOS, it needs none if you use the Terminal command below. The macOS
**Open Anyway** route asks for an administrator's password. On a Mac where you have no such
password, use the Terminal command.

**Windows.** The steps after this one assume the suggested folder, `C:\Users\<you>\Tether`. If
you choose another folder, use that path in its place.

1. Double-click the `.exe`.
2. When SmartScreen says *"Windows protected your PC"*, choose **More info → Run anyway**.
3. Accept the license.
4. Choose **Just Me**.
5. Keep the suggested destination folder, unless you have a reason to change it.

**macOS.** The most reliable route is one Terminal command. Run it in the download folder. On
an Intel Mac, use the `x86_64` name.

```bash
installer -pkg Tether-1.0.0-rc3-MacOSX-arm64.pkg -target CurrentUserHomeDirectory
```

It installs into `~/Tether`, the `Tether` folder in your home folder. It asks for no password.
Tether's own macOS build check runs this exact command.

You can also double-click the `.pkg` and choose to install for yourself only. If macOS blocks
it, use **System Settings → Privacy & Security → Open Anyway**. The troubleshooting entry
linked above tells you how.

**Linux.** The install location **must not contain a space**. The installer refuses a location
with a space. Run the installer with `sh`:

```bash
sh Tether-1.0.0-rc3-Linux-x86_64.sh
```

1. Page through the license.
2. Type `yes`.
3. Press Enter to accept the suggested location, `~/tether` (lower-case, in your home folder).
4. Wait for `installation finished.`

Installing unpacks about 3.6 GB. On a Linux test machine, it took around a minute and a half.

### Why the installers are unsigned

Signing an installer takes a certificate from Apple or Microsoft. Both charge for it every
year. At the prices they listed on 10 November 2026, that is roughly **US$220 a year**. That is
US$99 for the [Apple Developer Program](https://developer.apple.com/support/purchase-activation)
and about US$9.99 a month for Microsoft's
[Artifact Signing](https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options).
Individuals can use Artifact Signing only in the USA and Canada. A conventional Windows
certificate costs roughly US$150–300 a year instead.

The free program for open-source projects,
SignPath Foundation, declined Tether in July 2026. The reason was that the project is still
young. So for now the installers ship unsigned. The checks in step 2 are what tell you that a
download is genuine.

Signing would change more on macOS than on Windows. A signed and notarized macOS installer opens
without the Gatekeeper block. To get there, every program inside the installer must also be
signed. That work is still to be done.

A signed Windows installer shows a verified publisher
name instead of *"Unknown publisher"*. But Microsoft says SmartScreen
[can still warn](https://learn.microsoft.com/windows/apps/package-and-deploy/smartscreen-reputation)
about a new release. It can warn until the release has built up a download reputation.

**If Tether is useful to you, please consider
[sponsoring it](https://github.com/sponsors/bioedca).** Sponsorship pays for Tether's
development, and code signing comes first. Then macOS stops blocking the installer, and Windows
names who published it. When a funding goal for signing is set, that Sponsors page shows its
progress.

## 4. Open a terminal where `tether` works

The installer creates a `bin` folder inside the install folder. It holds two launchers:
`tether` (the command-line tool) and `tether-gui` (the desktop application). The installer
deliberately **does not edit your PATH, shell settings or registry**. So a new terminal cannot
find those launchers by their names alone. Do the step for your system.

**Windows.** From the next release, the Start menu has a **Tether Command Prompt**
([#666](https://github.com/bioedca/tether/issues/666)).

1. Open the Start menu.
2. Choose **Tether Command Prompt**.

It opens a console in which `tether` works. Its first line shows the Tether version. Only that
console finds the launchers. Your PATH does not change.

The rc3 release has no such shortcut. With rc3, run the launcher by its full path in
PowerShell:

```powershell
& "$env:USERPROFILE\Tether\bin\tether.bat" --version
```

**macOS and Linux.** Run the launcher by its full path. This needs no change to a shell file:

```bash
~/Tether/bin/tether --version    # macOS
~/tether/bin/tether --version    # Linux
```

From the next release, the launchers are in a folder named `launchers` instead of `bin`
([#492](https://github.com/bioedca/tether/issues/492)). The commands are then
`~/Tether/launchers/tether --version` and `~/tether/launchers/tether --version`.

To type `tether` with no path in any terminal, put the launcher folder on your PATH. The next
section tells you how. That step is optional on all three systems.

### Optional: put the launcher folder on your PATH

Run the line for your system. Then **close the terminal and open a new one**.

**Linux** (bash):

```bash
echo 'export PATH="$PATH:$HOME/tether/bin"' >> ~/.bashrc
```

**macOS** (zsh, the default Terminal shell):

```bash
echo 'export PATH="$PATH:$HOME/Tether/bin"' >> ~/.zshrc
```

**Windows** (in PowerShell). This line changes your own user PATH, not the system one:

```powershell
[Environment]::SetEnvironmentVariable("Path",
  [Environment]::GetEnvironmentVariable("Path", "User") + ";$env:USERPROFILE\Tether\bin",
  "User")
```

The folder goes at the **end** of your PATH on purpose. In rc3, on Linux and macOS, the folder
also holds the installer's own copies of `python`, `pip` and `conda`. At the end of your PATH,
the copies of those programs that you already use keep working exactly as before.

**From the next release**, the installer puts the two launchers in a folder of their own,
`launchers` ([#492](https://github.com/bioedca/tether/issues/492)). That folder holds nothing
else. With that release, write `launchers` in place of `bin` in the line for your system. For
example, write `$HOME/tether/launchers` on Linux.

## 5. Check that Tether runs

If you used neither the **Tether Command Prompt** nor the PATH step, run the full-path command
from step 4 instead. Otherwise, in the **Tether Command Prompt** or in a **new**
terminal, run:

```bash
tether --version
```

It prints `tether` and the version. For this release, it prints:

```text
tether 1.0.0rc3
```

If it prints exactly that version, the install worked and your terminal finds its launcher.

If it prints a **different** version, an older Tether comes earlier on your `PATH`. A source
checkout is one example. That older Tether is the one that answers. Remove it from your `PATH`,
or run the new launcher by its full path:

- Linux: `~/tether/bin/tether --version`
- macOS: `~/Tether/bin/tether --version`
- Windows PowerShell: `& "$env:USERPROFILE\Tether\bin\tether.bat" --version`

To start the desktop application:

- **Windows:** run `tether-gui` in a terminal, or run `C:\Users\<you>\Tether\bin\tether-gui.bat`.
  The rc3 installer creates a **Tether** Start menu shortcut, but Start does not show it
  ([#578](https://github.com/bioedca/tether/issues/578)). The next release fixes this. If you
  find a **Tether** entry in the Start menu, you can also choose it there. From the next
  release, that entry also opens no console window
  ([#579](https://github.com/bioedca/tether/issues/579)).
- **Linux:** choose **Tether** from your applications menu, or run `tether-gui` in a terminal.
  If you skipped the optional PATH step, run `~/tether/bin/tether-gui`. From the next release,
  that path is `~/tether/launchers/tether-gui`. A desktop that was already running may show the
  menu entry only after you log out and back in.
- **macOS:** with rc3, run `tether-gui` in Terminal. If you skipped the optional PATH step, run
  `~/Tether/bin/tether-gui`. The Dock shows the Tether icon while the application runs. From
  the next release, the installer also puts **Tether** in the `Applications` folder of your
  home folder ([#663](https://github.com/bioedca/tether/issues/663)). Open it from
  **Launchpad**, find it with **Spotlight**, or double-click it in Finder. To find it with
  Spotlight, press Command-Space and type `Tether`. You can keep it in the Dock. `tether-gui`
  in Terminal still works and is the fallback. If you skipped the optional PATH step, the
  fallback is `~/Tether/launchers/tether-gui`. If macOS blocks the first start of **Tether**,
  see [If macOS will not open Tether.app](troubleshooting.md#if-macos-will-not-open-tetherapp).

While the application loads, a splash window shows the Tether name, the version and
**Loading…**. The splash window closes when the main window opens.

The application opens with no project loaded. It shows the start page, with the Tether art and
the tagline at the top. To open a project, do one of these:

- Click **Open project…** on the start page.
- Use **File → Open project…**.
- Drop a `.tether` file on the application window.

**Help → About Tether** shows the version, the commit, the two licenses and the citation with
the DOI. It also shows links to the documentation and the source code. Its **Copy** button
copies this text for a bug report. The [Command line](cli.md) reference tells what `tether`
itself can do.

From the next release, a double-click on a `.tether` project opens it in Tether
([#664](https://github.com/bioedca/tether/issues/664)). This works in Explorer, in Finder and in
your Linux file manager.

- On Windows and Linux, each double-click starts a new Tether window.
- On macOS, a running Tether opens the project in its own window, as **File → Open project…**
  does. It closes the project that was open first. If a dialog is open, the project opens when
  you close the dialog.
- If another Tether window has the project open, the project opens read-only. A
  **Read-only project** banner tells you why.
- On Windows, the installer leaves `.tether` files with a program that already opens them.
  Tether is then in the **Open with** list of the file.

## What gets installed, and where

Everything lives in the single folder that you chose. By default, that is `~/tether` on Linux,
`~/Tether` on macOS and `C:\Users\<you>\Tether` on Windows:

| Inside the install folder | What it is |
|---|---|
| `bin/tether`, `bin/tether-gui` (on Windows, `bin\tether.bat`, `bin\tether-gui.bat`) | The two launchers this page puts on your PATH. From the next release they are in `launchers/` (on Windows, `launchers\`), a folder that holds only them. |
| `envs/tether` | The Tether application and everything it runs on. |
| `envs/sidecar` | A separate, isolated copy of tMAVEN used for one-click idealization. It is kept apart because it needs older library versions than the application. |
| everything else | The small installer toolkit used once, at install time, to lay down the two folders above. It holds no Tether code. |

Outside the install folder, the installer adds one menu entry:

- On Windows, a **Tether** Start menu shortcut. From the next release, a
  **Tether Command Prompt** shortcut is beside it. It runs `tether-prompt.bat` from the install
  folder.
- On Linux, an application-menu entry at `~/.local/share/applications/tether.desktop`, with its
  icon. The icon is nine files in `~/.local/share/icons/hicolor`. Eight are `tether.png`, in
  the `apps` folder of each size folder: `16x16`, `24x24`, `32x32`, `48x48`, `64x64`,
  `128x128`, `256x256` and `512x512`. The ninth is `tether.svg` in `scalable/apps`.
- On macOS, from the next release, the application `Tether.app` in `~/Applications`. It is in
  `/Applications` instead when you installed into a folder outside your home folder.
  `Tether.app` is a small launcher. It starts the application in the install folder and holds
  no copy of it.

From the next release the installer also registers the `.tether` file type:

- On Windows, it writes the `.tether` and `Tether.Project` keys under `Software\Classes` in the
  registry. They are for your user only, or for all users when you installed for all users.
- On Linux, it writes the file `~/.local/share/mime/packages/tether.xml`. It then updates the
  user MIME database in `~/.local/share/mime` with `update-mime-database`.
- On macOS, `Tether.app` itself declares the file type.

Windows and macOS also keep their usual record of an installed program. On Windows, that is the
**Installed apps** entry that the uninstaller runs from. On macOS, it is the package receipt
that macOS records for every `.pkg`. Tether itself can also write the
[settings file](#settings-tether-keeps-outside-the-install-folder) below, but only after you
answer its question.

On a Linux test install of this release, the folder came to **3.6 GB**. The other systems have
not been measured yet.

## Settings Tether keeps outside the install folder

Tether keeps two application settings outside any `.tether` project:

- Your answer to the question whether Tether may check for a newer release.
- Your selection in **View → Trace colors**: color-blind safe (the default) or classic. See
  [Trace colors](reference/gui-theme.md#select-the-trace-colors).

The first time the application starts, a bar at the top of the window asks a question. Its two
answers are **Check for new releases** and **Don't check**. Until you answer yes, Tether makes
no network connection. If you answer yes, Tether asks GitHub once at each start whether a newer
release exists. If one does, a notice links to the
[Releases page](https://github.com/bioedca/tether/releases). Tether downloads nothing.

To change the answer, use **Help → Check for new releases at startup**. The
[privacy policy](privacy.md) says exactly what the request sends.

The settings are outside the install folder, so a newer release installed over the old one does
not lose them. They are in a small file in your own settings folder. An installer can replace
or delete the install folder, so a file there could be lost on an update.

| Your computer | Settings file |
|---|---|
| Windows | `%LOCALAPPDATA%\MondragonLab\Tether\settings.json` |
| macOS | `~/Library/Application Support/MondragonLab/Tether/settings.json` |
| Linux | `~/.config/tether/settings.json` (or `$XDG_CONFIG_HOME/tether/settings.json` if you set `XDG_CONFIG_HOME` to an absolute path) |

The file holds at most three values. They are a format number, your answer (`allowed` or
`declined`) and your trace colors (`safe` or `classic`). Each value is there only after you give
it. The file holds no name, no computer name, no time, no file path and no project data.

If the file is missing, empty or damaged, Tether reads it as *no answer yet*. Damaged means
unreadable, not JSON, or another format number. It makes no network connection, and the traces use the
default colors. Tether never reads a damaged file as *allowed*.

In a readable file, Tether reads each value on its own. An unknown trace-color value gives the
default colors and keeps a valid answer to the update question. An unknown answer keeps the
trace colors. A change of trace colors never writes an answer to the update question.

Beside the file sits an empty file, `settings.json.lock`. It lets two open Tether windows take
turns saving, so neither loses what the other saved. If the settings file is there but Tether
cannot read it, Tether does not save over it. Its permissions are one possible cause. A new
answer or color then applies only until Tether closes. The status bar says that it was not
saved.

### For a site administrator: turn the update check off for every user

On a shared computer, an administrator can turn the update check off for every user. This
applies whatever each user answers. To do this, create an empty file with this name:

| Computer | File to create |
|---|---|
| Windows | `%PROGRAMDATA%\MondragonLab\Tether\disable-update-check` (normally `C:\ProgramData\MondragonLab\Tether\disable-update-check`) |
| macOS | `/Library/Application Support/MondragonLab/Tether/disable-update-check` |
| Linux | `/etc/tether/disable-update-check` |

The file can be empty. Tether does not read what is in it. While it is there, Tether makes no
update request and does not ask the question. **Help → Check for new releases at startup**
is greyed out.

If Tether cannot examine the file, for example because a folder permission stops
it, Tether also turns the check off. This file can only turn the check off. Nothing lets an
administrator turn the check on for a user who did not agree.

For example, on Linux:

```bash
sudo mkdir -p /etc/tether
sudo touch /etc/tether/disable-update-check
```

On Windows, in a PowerShell window opened with **Run as administrator**:

```powershell
New-Item -ItemType Directory -Force "$env:ProgramData\MondragonLab\Tether" | Out-Null
New-Item -ItemType File -Force "$env:ProgramData\MondragonLab\Tether\disable-update-check" | Out-Null
```

The override applies to the Tether application, not to a user who changes the files of their
own install folder. Each user owns their own install, so the override is a site setting, not a
lock. To remove it, delete the file.

## If it did not work

**`tether: command not found`, or *"is not recognized as the name of a cmdlet"*.** The
terminal cannot see the launcher folder. On Windows, from the next release, open the
**Tether Command Prompt** from the Start menu. Type the command there.

On any system, you can
run the launcher by its full path, as step 4 shows. If you did the optional PATH step, check
that you opened a **new** terminal after it. Also check that the folder in the PATH line
matches where you installed.

**The application does not open.** Tether does not write a log file. So start it from a
terminal with `tether-gui`, and read what it prints. The macOS **Tether** application and the
Windows Start menu shortcut show no text when the start fails. On Windows, the Start menu
shortcut and `tether-gui` show no console. So run this command instead to see the error text:

```powershell
& "$env:USERPROFILE\Tether\envs\tether\python.exe" -m tether.gui
```

**Linux: *"Could not load the Qt platform plugin "xcb""*.** The full message looks like this:

```text
qt.qpa.plugin: From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt xcb platform plugin.
qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.
This application failed to start because no Qt platform plugin could be initialized. Reinstalling the application may fix this problem.
```

Reinstalling will not fix it. Two causes produce it:

- There is no graphical desktop, for example on a server or in an SSH session without display
  forwarding. Use the `tether` command line instead, which needs no display.
- The system is missing a library that the installer cannot ship. On Debian and Ubuntu, add it
  with `sudo apt install libxcb-cursor0`. On Fedora, add it with
  `sudo dnf install xcb-util-cursor`.

Everything else, keyed by the message you saw, is in [Troubleshooting](troubleshooting.md).

## Uninstall

Tether keeps to its one folder, so removing it is mostly deleting that folder. If you did the
optional PATH step in step 4, remove that PATH line too.

> **Check the folder first.** Deleting the install folder deletes *everything* in it. If you
> ever saved a `.tether` project, a movie or an export inside it, move those out before you
> uninstall. Nothing below asks before it deletes them.

**Linux**

```bash
rm -rf ~/tether ~/.local/share/applications/tether.desktop
rm -f ~/.local/share/icons/hicolor/{16x16,24x24,32x32,48x48,64x64,128x128,256x256,512x512}/apps/tether.png
rm -f ~/.local/share/icons/hicolor/scalable/apps/tether.svg
rm -f ~/.local/share/mime/packages/tether.xml
command -v update-mime-database >/dev/null && update-mime-database ~/.local/share/mime
```

The last two lines remove the `.tether` file type, which installers from the next release add.

If you did the optional PATH step, delete the `export PATH=…/tether/bin` line from
`~/.bashrc`. From the next release, that line ends in `…/tether/launchers`.

**macOS**

If you installed into a folder outside your home folder, use that folder in both commands
below. Also put `sudo` before the first command, because the launcher is then in
`/Applications`.

First, remove the **Tether** application (`Tether.app`) that this install added:

```bash
~/Tether/envs/tether/bin/python -m tether.gui.macos_app remove --prefix ~/Tether
```

It removes a `Tether.app` only when it is Tether's launcher for this install folder. Another
program's `Tether.app` stays. If it says `could not remove`, run it again with `sudo` before you
go on. With rc3, it says `No module named tether.gui.macos_app`. The rc3 release adds no
`Tether.app`, so go on to the next command.

Then delete the install folder:

```bash
rm -rf ~/Tether
```

If you did the optional PATH step, delete the `export PATH=…/Tether/bin` line from
`~/.zshrc`. From the next release, that line ends in `…/Tether/launchers`. The package receipt
for the install stays on macOS. It is a small record, and it does no harm.

**Windows**

1. Open **Settings → Apps → Installed apps**.
2. Find **Tether**.
3. Choose **Uninstall**.

The uninstaller also removes the Start menu shortcuts and the `.tether` file type. The
shortcuts are **Tether** and, from the next release, **Tether Command Prompt**. An installer
from 1.0.0-rc2 or earlier does not remove them. If a **Tether** folder is still in the Start
menu after you uninstall such a release, delete it.

If you did the optional PATH step, remove `C:\Users\<you>\Tether\bin` from your user PATH. From
the next release, remove `C:\Users\<you>\Tether\launchers` instead. The user PATH is under
**Settings → System → About → Advanced system settings → Environment Variables**.

Projects, movies and exports saved anywhere **outside** the install folder are not touched
by any of these steps.

These steps do not touch the [settings file](#settings-tether-keeps-outside-the-install-folder)
either, so a later install keeps your answer. To remove it too, delete the `MondragonLab\Tether`
folder on Windows and macOS. On Linux, delete the `tether` folder in `$XDG_CONFIG_HOME` if you
set that variable to an absolute path. Otherwise, delete `~/.config/tether`.

## The optional GPU add-on

The deep trace classifier is an optional extra for users with an NVIDIA GPU. It lets them train
or run a deep model on their own labels. It is **not** part of the installer, and Tether does
not need it. [Deep trace classifier](ml/deep-classifier.md) tells you how to set it up.

## The optional Bio-Formats stack (cellSens `.vsi` movies)

Tether reads Olympus/Evident cellSens `.vsi` movies through
[Bio-Formats](https://www.openmicroscopy.org/bio-formats/). Bio-Formats runs on Java. Java is
**not** part of the installer. Tether needs it for no other file. The Bio-Formats stack is a
separate environment, kept apart from the application like the tMAVEN copy
([ADR-0147](adr/0147-cellsens-vsi-movies-are-read-by-bio-formats-in-an-isolated-fourth-stack.md)).
Install it only if you open `.vsi` files.

1. Get `bioformats/conda-lock.yml` from the Tether repository, at the version of your Tether.
   The [source on GitHub](https://github.com/bioedca/tether/tree/main/bioformats) has it. The
   lock names each package by version and checksum, for Linux, macOS and Windows.
2. Make the environment in a folder of your choice with
   [micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html):

    ```sh
    micromamba create -p ~/tether-bioformats -f bioformats/conda-lock.yml
    ```

    The command `conda-lock install -p <folder> bioformats/conda-lock.yml` does the same. The
    stack takes about 750 MB of disk on Linux. It holds Python 3.12, an OpenJDK 21 Java runtime,
    JPype and Bio-Formats 8.0. Bio-Formats comes from the bioconda channel as `bftools`.

3. Tell Tether where the environment's Python is. Set the `TETHER_BIOFORMATS_PYTHON`
   environment variable in the terminal (or the user environment) that starts Tether:

    ```sh
    # Linux and macOS
    export TETHER_BIOFORMATS_PYTHON=~/tether-bioformats/bin/python
    ```

    ```powershell
    # Windows (PowerShell)
    $env:TETHER_BIOFORMATS_PYTHON = "$HOME\tether-bioformats\python.exe"
    ```

Open a `.vsi` file as any other movie. Keep the `_<name>_` folder that cellSens writes beside
the `.vsi` file. The frames are in the `.ets` files inside it.

Without the stack, Tether refuses
a `.vsi` with a message that gives these steps. Tether also refuses frames stored with JPEG or
JPEG 2000, which can be lossy. Export such an acquisition again without compression.
[Troubleshooting](troubleshooting.md) lists the messages.

To remove the stack, delete its folder and unset the variable.
